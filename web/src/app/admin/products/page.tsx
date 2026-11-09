"use client";

import { useCallback, useEffect, useState } from "react";

import { useAdmin } from "../admin-shell";
import { api, ApiError } from "@/lib/api";
import { formatBaht, parseBaht } from "@/lib/money";

interface Row {
  sku: string;
  name: string;
  price_satang: number;
  stock_remaining: number | null;
}

export default function ProductsPage() {
  const admin = useAdmin();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const result = await api<{ items: Row[] }>("/api/v1/admin/products");
    setRows(result.items);
  }, []);

  useEffect(() => {
    void load().catch(() => setMessage("โหลดข้อมูลไม่สำเร็จ"));
  }, [load]);

  async function save(row: Row, name: string, priceText: string) {
    const price = parseBaht(priceText);
    if (price === null) return setMessage("ราคาไม่ถูกต้อง");
    try {
      await api(`/api/v1/admin/products/${encodeURIComponent(row.sku)}`, {
        method: "PATCH",
        json: { name, price_satang: price },
        headers: { "X-CSRF-Token": admin.csrf_token },
      });
      setMessage(`บันทึก ${row.sku} แล้ว`);
      await load();
    } catch (failure) {
      setMessage(failure instanceof ApiError ? failure.message : "บันทึกไม่สำเร็จ");
    }
  }

  if (rows === null) return <div className="skeleton" aria-busy="true" />;
  return (
    <div className="stack">
      <h1>สินค้า</h1>
      <p aria-live="polite">{message}</p>
      {rows.map((row) => (
        <ProductRow key={`${row.sku}:${row.name}:${row.price_satang}`} row={row} onSave={save} />
      ))}
    </div>
  );
}

function ProductRow({
  row,
  onSave,
}: {
  row: Row;
  onSave: (row: Row, name: string, price: string) => Promise<void>;
}) {
  const [name, setName] = useState(row.name);
  const [price, setPrice] = useState(formatBaht(row.price_satang).replaceAll(",", ""));
  return (
    <form
      className="card filters"
      onSubmit={(event) => {
        event.preventDefault();
        void onSave(row, name, price);
      }}
    >
      <div className="field">
        <label className="field-label" htmlFor={`name-${row.sku}`}>
          {row.sku}
        </label>
        <input
          id={`name-${row.sku}`}
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="field">
        <label className="field-label" htmlFor={`price-${row.sku}`}>
          ราคา (บาท)
        </label>
        <input
          id={`price-${row.sku}`}
          className="input"
          inputMode="decimal"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
        />
      </div>
      <button className="btn" type="submit">
        บันทึก
      </button>
    </form>
  );
}
