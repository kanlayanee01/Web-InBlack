"use client";

import { use, useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { useAdmin } from "../../admin-shell";
import { Toast } from "@/components/toast";
import { Field, Spinner } from "@/components/ui";
import { api, ApiError, randomKey } from "@/lib/api";
import { formatBaht, parseBaht } from "@/lib/money";

interface Detail {
  id: string;
  status: string;
  email: string;
  game_uid: string;
  total_satang: number;
  paid_satang: number;
  refunded_satang: number;
  items: { sku: string; name: string; qty: number; unit_price_satang: number }[];
  timeline: Record<string, string | number | null>[];
  webhooks: Record<string, string | number | null>[];
  fulfillment_attempts: Record<string, string | number | null>[];
  refunds: Record<string, string | number | null>[];
}

const REFUNDABLE = ["COMPLETED", "FAILED", "NEEDS_REVIEW", "EXPIRED"];
const RETRYABLE = ["FAILED", "NEEDS_REVIEW"];

function Rows({
  title,
  rows,
  columns,
}: {
  title: string;
  rows: Record<string, string | number | null>[];
  columns: string[];
}) {
  return (
    <section className="stack">
      <h2>{title}</h2>
      {rows.length === 0 ? (
        <p className="muted">ไม่มีข้อมูล</p>
      ) : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={title}>
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column}>{String(row[column] ?? "-")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function OrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const orderId = decodeURIComponent(id);
  const admin = useAdmin();
  const [order, setOrder] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const closeToast = useCallback(() => setMessage(null), []);

  const load = useCallback(async () => {
    try {
      setOrder(await api<Detail>(`/api/v1/admin/orders/${encodeURIComponent(orderId)}`));
    } catch {
      setError("โหลดออเดอร์ไม่สำเร็จ");
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function retry() {
    setMessage(null);
    setError(null);
    try {
      await api(`/api/v1/admin/orders/${encodeURIComponent(orderId)}/retry-fulfillment`, {
        method: "POST",
        headers: { "X-CSRF-Token": admin.csrf_token },
      });
      setMessage("ส่งเข้าคิวเติมเกมอีกครั้งแล้ว");
      await load();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "สั่งเติมใหม่ไม่สำเร็จ");
    }
  }

  if (order === null) {
    return error ? (
      <p className="alert" role="alert">
        {error}
      </p>
    ) : (
      <div className="skeleton" aria-busy="true" />
    );
  }

  const balance = order.paid_satang - order.refunded_satang;
  return (
    <div className="stack">
      <h1>{order.id}</h1>
      <section className="card stack">
        <p>
          สถานะ <strong>{order.status}</strong>
        </p>
        <p>อีเมล {order.email}</p>
        <p>Game UID {order.game_uid}</p>
        <p>
          ยอด ฿{formatBaht(order.total_satang)} · จ่ายแล้ว ฿{formatBaht(order.paid_satang)} ·
          คืนแล้ว ฿{formatBaht(order.refunded_satang)}
        </p>
        {order.items.map((item) => (
          <p key={item.sku}>
            {item.name} x{item.qty} (฿{formatBaht(item.unit_price_satang)})
          </p>
        ))}
        {error ? (
          <p className="alert" role="alert">
            {error}
          </p>
        ) : null}
        <div className="row">
          {admin.can("support") && RETRYABLE.includes(order.status) ? (
            <button className="btn btn-secondary" type="button" onClick={() => void retry()}>
              เติมเกมอีกครั้ง
            </button>
          ) : null}
          {admin.can("finance") && REFUNDABLE.includes(order.status) && balance > 0 ? (
            <RefundDialog
              orderId={order.id}
              balance={balance}
              csrf={admin.csrf_token}
              onDone={() => {
                setMessage("บันทึกการคืนเงินแล้ว");
                void load();
              }}
            />
          ) : null}
        </div>
      </section>
      <Rows
        title="Timeline"
        rows={order.timeline}
        columns={["seq", "from_status", "to_status", "actor", "reason", "created_at"]}
      />
      <Rows
        title="Webhook"
        rows={order.webhooks}
        columns={["event_id", "type", "amount_satang", "outcome", "received_at"]}
      />
      <Rows
        title="ประวัติการเติมเกม"
        rows={order.fulfillment_attempts}
        columns={["reference", "action", "outcome", "http_status", "detail", "created_at"]}
      />
      <Rows
        title="การคืนเงิน"
        rows={order.refunds}
        columns={["amount_satang", "status", "reason", "actor", "created_at"]}
      />
      <Toast message={message} onClose={closeToast} />
    </div>
  );
}

function RefundDialog({
  orderId,
  balance,
  csrf,
  onDone,
}: {
  orderId: string;
  balance: number;
  csrf: string;
  onDone: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const key = useRef(randomKey());
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function close() {
    dialog.current?.close();
    setConfirmed(null);
    setError(null);
  }

  function review(event: FormEvent) {
    event.preventDefault();
    const satang = parseBaht(amount);
    if (satang === null || satang <= 0) return setError("กรอกจำนวนเงินให้ถูกต้อง");
    if (satang > balance) return setError(`คืนได้ไม่เกิน ฿${formatBaht(balance)}`);
    if (reason.trim() === "") return setError("กรุณาระบุเหตุผล");
    setError(null);
    key.current = randomKey();
    setConfirmed(satang);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/admin/orders/${encodeURIComponent(orderId)}/refund`, {
        method: "POST",
        json: { amount_satang: confirmed, reason: reason.trim() },
        headers: { "X-CSRF-Token": csrf, "Idempotency-Key": key.current },
      });
      close();
      onDone();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "คืนเงินไม่สำเร็จ");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        className="btn btn-danger"
        type="button"
        onClick={() => dialog.current?.showModal()}
      >
        คืนเงิน
      </button>
      <dialog ref={dialog} aria-labelledby="refund-title" onClose={close}>
        <h2 id="refund-title">{confirmed === null ? "คืนเงิน" : "ยืนยันการคืนเงิน"}</h2>
        {confirmed === null ? (
          <form className="stack" onSubmit={review} noValidate>
            <Field
              id="refund-amount"
              label="จำนวนเงิน (บาท)"
              hint={`คืนได้สูงสุด ฿${formatBaht(balance)}`}
            >
              {(describedBy) => (
                <input
                  id="refund-amount"
                  className="input"
                  inputMode="decimal"
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                  aria-describedby={describedBy}
                />
              )}
            </Field>
            <Field id="refund-reason" label="เหตุผล" error={error}>
              {(describedBy, invalid) => (
                <input
                  id="refund-reason"
                  className="input"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </Field>
            <div className="row">
              <button className="btn" type="submit">
                ถัดไป
              </button>
              <button className="btn btn-secondary" type="button" onClick={close}>
                ยกเลิก
              </button>
            </div>
          </form>
        ) : (
          <div className="stack">
            <p>
              คืนเงิน <strong>฿{formatBaht(confirmed)}</strong> ให้ออเดอร์ {orderId}
            </p>
            <p className="muted">เมื่อยืนยันแล้วจะยกเลิกไม่ได้</p>
            {error ? (
              <p className="alert" role="alert">
                {error}
              </p>
            ) : null}
            <div className="row">
              <button
                className="btn btn-danger"
                type="button"
                disabled={busy}
                onClick={() => void submit()}
              >
                {busy ? <Spinner /> : null} ยืนยันคืนเงิน
              </button>
              {/* Focus lands on the safe choice, so a stray Enter cannot confirm a refund. */}
              <button
                className="btn btn-secondary"
                type="button"
                autoFocus
                onClick={() => setConfirmed(null)}
              >
                ย้อนกลับ
              </button>
            </div>
          </div>
        )}
      </dialog>
    </>
  );
}
