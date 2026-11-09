"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatBaht } from "@/lib/money";

const REFRESH_MS = 2000;

interface Figures {
  orders_last_minute: number;
  sales_today_satang: number;
  queue_depth: number;
  dead_letter: number;
}

export default function DashboardPage() {
  const [figures, setFigures] = useState<Figures | null>(null);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const latest = await api<Figures>("/api/v1/admin/dashboard");
        if (active) {
          setFigures(latest);
          setStale(false);
        }
      } catch {
        if (active) setStale(true);
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  if (figures === null) return <div className="skeleton" aria-busy="true" />;
  const stats: [string, string][] = [
    ["ออเดอร์ต่อนาที", String(figures.orders_last_minute)],
    ["ยอดขายวันนี้", `฿${formatBaht(figures.sales_today_satang)}`],
    ["งานในคิว", String(figures.queue_depth)],
    ["Dead-letter", String(figures.dead_letter)],
  ];
  return (
    <div className="stack">
      <h1>แดชบอร์ด</h1>
      {stale ? <p className="notice">เชื่อมต่อไม่ได้ ตัวเลขอาจไม่เป็นปัจจุบัน</p> : null}
      <div className="stats">
        {stats.map(([label, value]) => (
          <div key={label} className="card">
            <div className="muted small">{label}</div>
            <div className="stat-value">{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
