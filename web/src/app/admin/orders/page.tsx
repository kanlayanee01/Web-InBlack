"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { formatBaht } from "@/lib/money";

const STATUSES = [
  "PENDING_PAYMENT",
  "PAID",
  "IN_QUEUE",
  "IN_PROGRESS",
  "COMPLETED",
  "EXPIRED",
  "PAYMENT_FAILED",
  "FAILED",
  "REFUNDED",
  "NEEDS_REVIEW",
];

interface Row {
  id: string;
  status: string;
  email: string;
  game_uid: string;
  payment_method: string;
  total_satang: number;
  created_at: string;
}

interface Filters {
  status: string;
  date_from: string;
  date_to: string;
  q: string;
}

const EMPTY: Filters = { status: "", date_from: "", date_to: "", q: "" };

function buildQuery(filters: Filters, cursor: string | null): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export default function OrdersPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (active: Filters, after: string | null) => {
    setFailed(false);
    try {
      const page = await api<{ items: Row[]; next_cursor: string | null }>(
        `/api/v1/admin/orders?${buildQuery(active, after)}`,
      );
      setRows((previous) => (after && previous ? [...previous, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    setRows(null);
    void load(filters, null);
  }, [filters, load]);

  function apply(event: FormEvent) {
    event.preventDefault();
    setFilters(draft);
  }

  function update(key: keyof Filters, value: string) {
    setDraft((previous) => ({ ...previous, [key]: value }));
  }

  return (
    <div className="stack">
      <h1>ออเดอร์</h1>
      <form className="card filters" onSubmit={apply}>
        <div className="field">
          <label className="field-label" htmlFor="filter-status">
            สถานะ
          </label>
          <select
            id="filter-status"
            className="input"
            value={draft.status}
            onChange={(event) => update("status", event.target.value)}
          >
            <option value="">ทั้งหมด</option>
            {STATUSES.map((status) => (
              <option key={status}>{status}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor="filter-from">
            ตั้งแต่วันที่
          </label>
          <input
            id="filter-from"
            className="input"
            type="date"
            value={draft.date_from}
            onChange={(event) => update("date_from", event.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="filter-to">
            ก่อนวันที่
          </label>
          <input
            id="filter-to"
            className="input"
            type="date"
            value={draft.date_to}
            onChange={(event) => update("date_to", event.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="filter-q">
            เลขออเดอร์หรืออีเมล
          </label>
          <input
            id="filter-q"
            className="input"
            value={draft.q}
            onChange={(event) => update("q", event.target.value)}
          />
        </div>
        <button className="btn" type="submit">
          กรอง
        </button>
      </form>

      {failed ? (
        <p className="alert" role="alert">
          โหลดข้อมูลไม่สำเร็จ
        </p>
      ) : rows === null ? (
        <div className="skeleton" aria-busy="true" />
      ) : rows.length === 0 ? (
        <p className="muted">ไม่พบออเดอร์</p>
      ) : (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="ตารางออเดอร์">
            <table>
              <thead>
                <tr>
                  <th scope="col">เลขออเดอร์</th>
                  <th scope="col">สถานะ</th>
                  <th scope="col">อีเมล</th>
                  <th scope="col">Game UID</th>
                  <th scope="col">ยอด</th>
                  <th scope="col">เวลา</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Link href={`/admin/orders/${encodeURIComponent(row.id)}`}>{row.id}</Link>
                    </td>
                    <td>{row.status}</td>
                    <td>{row.email}</td>
                    <td>{row.game_uid}</td>
                    <td>฿{formatBaht(row.total_satang)}</td>
                    <td>{new Date(row.created_at).toLocaleString("th-TH")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cursor ? (
            <button
              className="btn btn-secondary"
              type="button"
              disabled={loadingMore}
              onClick={async () => {
                setLoadingMore(true);
                await load(filters, cursor);
                setLoadingMore(false);
              }}
            >
              {loadingMore ? <Spinner /> : null} โหลดเพิ่ม
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
