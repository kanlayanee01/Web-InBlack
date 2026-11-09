"use client";

import { useState, type FormEvent } from "react";

import { useAdmin } from "../admin-shell";
import { Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatBaht } from "@/lib/money";

interface Item {
  category: string;
  event_id: string | null;
  order_id: string | null;
  system_satang: number | null;
  provider_satang: number | null;
  detail: string;
}

interface Result {
  row_count: number;
  mismatch_count: number;
  items: Item[];
}

const LABELS: Record<string, string> = {
  missing_in_system: "มีที่ provider แต่ไม่มีในระบบ",
  missing_at_provider: "มีในระบบแต่ไม่มีที่ provider",
  amount_mismatch: "ยอดไม่ตรง",
  status_mismatch: "สถานะไม่ตรง",
};

function money(value: number | null): string {
  return value === null ? "-" : `฿${formatBaht(value)}`;
}

export default function ReconciliationPage() {
  const admin = useAdmin();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (file === null) return setError("กรุณาเลือกไฟล์ CSV");
    const body = new FormData();
    body.append("file", file);
    setBusy(true);
    setError(null);
    try {
      setResult(
        await api<Result>("/api/v1/admin/reconciliation", {
          method: "POST",
          body,
          headers: { "X-CSRF-Token": admin.csrf_token },
        }),
      );
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "อัปโหลดไม่สำเร็จ");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>กระทบยอด</h1>
      <form className="card stack" onSubmit={upload} noValidate>
        <div className="field">
          <label className="field-label" htmlFor="settlement">
            ไฟล์ settlement CSV
          </label>
          <input
            id="settlement"
            className="input"
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          {error ? <span className="field-error">{error}</span> : null}
        </div>
        <button className="btn" type="submit" disabled={busy}>
          {busy ? <Spinner /> : null} ตรวจสอบ
        </button>
      </form>
      {result ? (
        <section className="stack" aria-live="polite">
          <p>
            ตรวจ {result.row_count} แถว พบรายการไม่ตรง <strong>{result.mismatch_count}</strong>{" "}
            รายการ
          </p>
          {Object.entries(LABELS).map(([category, label]) => {
            const items = result.items.filter((item) => item.category === category);
            if (items.length === 0) return null;
            return (
              <div key={category} className="stack">
                <h2>
                  {label} ({items.length})
                </h2>
                <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Event</th>
                        <th scope="col">ออเดอร์</th>
                        <th scope="col">ในระบบ</th>
                        <th scope="col">Provider</th>
                        <th scope="col">รายละเอียด</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((item, index) => (
                        <tr key={index}>
                          <td>{item.event_id ?? "-"}</td>
                          <td>{item.order_id ?? "-"}</td>
                          <td>{money(item.system_satang)}</td>
                          <td>{money(item.provider_satang)}</td>
                          <td className="wrap-cell">{item.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </section>
      ) : null}
    </div>
  );
}
