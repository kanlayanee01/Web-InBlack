"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Product } from "@/lib/types";

const POLL_MS = 3000;

interface Props {
  slug: string;
  initial: number | null;
  labels: { stock: string; unlimited: string; soldOut: string };
}

export function LiveStock({ slug, initial, labels }: Props) {
  const [stock, setStock] = useState(initial);

  useEffect(() => {
    if (initial === null) return;
    let active = true;
    const refresh = async () => {
      try {
        const product = await api<Product>(`/api/v1/products/${encodeURIComponent(slug)}`);
        if (active) setStock(product.stock_remaining);
      } catch {
        // Keep the last known figure and try again on the next tick.
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [slug, initial]);

  if (stock === null) return <p>{labels.unlimited}</p>;
  if (stock === 0) return <p className="field-error">{labels.soldOut}</p>;
  return (
    <p>
      {labels.stock} <strong>{stock}</strong>
    </p>
  );
}
