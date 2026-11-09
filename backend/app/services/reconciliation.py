"""Compares a provider settlement file with our ledger, event by event."""

import csv
import io
import json
import re
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.domain.errors import DomainError

HEADER = ["event_id", "order_id", "type", "amount_satang", "settled_at"]
KIND_BY_TYPE = {"payment.succeeded": "payment", "payment.refunded": "refund"}
UNPAID_STATUSES = {"PENDING_PAYMENT", "PAYMENT_FAILED", "EXPIRED"}
_AMOUNT = re.compile(r"[0-9]{1,15}")


@dataclass(frozen=True)
class SettlementRow:
    event_id: str
    order_id: str
    type: str
    amount_satang: int
    settled_at: str


@dataclass(frozen=True)
class Mismatch:
    category: str
    event_id: str | None
    order_id: str | None
    system_satang: int | None
    provider_satang: int | None
    detail: str


def parse_settlement(text: str) -> list[SettlementRow]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    if next(reader, None) != HEADER:
        raise DomainError("INVALID_CSV", f"The header must be {','.join(HEADER)}")
    rows: list[SettlementRow] = []
    for line_no, cells in enumerate(reader, start=2):
        if not cells:
            continue
        if len(cells) != len(HEADER) or _AMOUNT.fullmatch(cells[3]) is None:
            raise DomainError("INVALID_CSV", f"Line {line_no} is not valid")
        rows.append(SettlementRow(cells[0], cells[1], cells[2], int(cells[3]), cells[4]))
    return rows


def compare(
    rows: list[SettlementRow],
    ledger: dict[str, asyncpg.Record],
    pending_refunds: list[asyncpg.Record],
) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    seen: set[str] = set()
    for row in rows:
        entry = ledger.get(row.event_id)
        if row.event_id in seen:
            mismatches.append(
                Mismatch(
                    "status_mismatch",
                    row.event_id,
                    row.order_id,
                    None,
                    row.amount_satang,
                    "The event appears more than once in the file",
                )
            )
            continue
        seen.add(row.event_id)
        if entry is None:
            mismatches.append(
                Mismatch(
                    "missing_in_system",
                    row.event_id,
                    row.order_id,
                    None,
                    row.amount_satang,
                    "The provider settled this event but our ledger has no record of it",
                )
            )
            continue
        mismatches.extend(_compare_entry(row, entry))

    for event_id, entry in ledger.items():
        if event_id not in seen:
            mismatches.append(
                Mismatch(
                    "missing_at_provider",
                    event_id,
                    entry["order_id"],
                    entry["amount_satang"],
                    None,
                    "Our ledger has this event but the settlement file does not",
                )
            )
    for refund in pending_refunds:
        mismatches.append(
            Mismatch(
                "missing_at_provider",
                None,
                refund["order_id"],
                refund["amount_satang"],
                None,
                "A refund is pending and the provider has not confirmed it",
            )
        )
    return mismatches


def _compare_entry(row: SettlementRow, entry: asyncpg.Record) -> list[Mismatch]:
    found: list[Mismatch] = []
    if row.amount_satang != entry["amount_satang"]:
        found.append(
            Mismatch(
                "amount_mismatch",
                row.event_id,
                entry["order_id"],
                entry["amount_satang"],
                row.amount_satang,
                "The settled amount differs from our ledger",
            )
        )
    if entry["kind"] == "payment" and entry["amount_satang"] != entry["total_satang"]:
        found.append(
            Mismatch(
                "amount_mismatch",
                row.event_id,
                entry["order_id"],
                entry["total_satang"],
                entry["amount_satang"],
                "The amount paid differs from the order total",
            )
        )
    wrong_kind = KIND_BY_TYPE.get(row.type) != entry["kind"]
    wrong_order = row.order_id != entry["order_id"]
    unpaid = entry["kind"] == "payment" and entry["status"] in UNPAID_STATUSES
    if wrong_kind or wrong_order or unpaid:
        found.append(
            Mismatch(
                "status_mismatch",
                row.event_id,
                entry["order_id"],
                entry["amount_satang"],
                row.amount_satang,
                f"Provider says {row.type}, order {row.order_id}; "
                f"we have {entry['kind']}, order is {entry['status']}",
            )
        )
    return found


async def run(
    conn: asyncpg.Connection, rows: list[SettlementRow], file_name: str, actor: str
) -> dict[str, object]:
    """Must run inside a transaction."""
    entries = await conn.fetch(
        """
        SELECT l.provider_event_id, l.order_id, l.kind, l.amount_satang, o.status, o.total_satang
        FROM ledger_entries l JOIN orders o ON o.id = l.order_id
        WHERE l.provider_event_id IS NOT NULL
        """
    )
    pending = await conn.fetch(
        "SELECT order_id, amount_satang FROM refunds WHERE status = 'PENDING'"
    )
    mismatches = compare(rows, {e["provider_event_id"]: e for e in entries}, list(pending))
    run_id: UUID = await conn.fetchval(
        """
        INSERT INTO reconciliation_runs (uploaded_by, file_name, row_count, mismatch_count)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        actor,
        file_name,
        len(rows),
        len(mismatches),
    )
    await conn.executemany(
        """
        INSERT INTO reconciliation_items (run_id, category, event_id, order_id, system_satang,
                                          provider_satang, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            (
                run_id,
                m.category,
                m.event_id,
                m.order_id,
                m.system_satang,
                m.provider_satang,
                m.detail,
            )
            for m in mismatches
        ],
    )
    await conn.execute(
        "INSERT INTO audit_logs (actor, action, target, detail) VALUES ($1, $2, $3, $4::jsonb)",
        actor,
        "reconciliation.run",
        str(run_id),
        json.dumps({"rows": len(rows), "mismatches": len(mismatches)}),
    )
    return await load_run(conn, run_id)


async def load_run(conn: asyncpg.Connection, run_id: UUID) -> dict[str, object]:
    head = await conn.fetchrow("SELECT * FROM reconciliation_runs WHERE id = $1", run_id)
    if head is None:
        raise DomainError("NOT_FOUND", "Reconciliation run not found")
    items = await conn.fetch(
        """
        SELECT category, event_id, order_id, system_satang, provider_satang, detail
        FROM reconciliation_items WHERE run_id = $1 ORDER BY category, id
        """,
        run_id,
    )
    summary: dict[str, int] = {}
    for item in items:
        summary[item["category"]] = summary.get(item["category"], 0) + 1
    return {
        "id": str(head["id"]),
        "file_name": head["file_name"],
        "uploaded_by": head["uploaded_by"],
        "row_count": head["row_count"],
        "mismatch_count": head["mismatch_count"],
        "created_at": head["created_at"].isoformat(),
        "summary": summary,
        "items": [dict(item) for item in items],
    }
