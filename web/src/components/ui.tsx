import type { ReactNode } from "react";

import { formatBaht } from "@/lib/money";
import type { Badge, Quote } from "@/lib/types";

export function StatusBadge({ badge, label }: { badge: Badge; label: string }) {
  return <span className={`badge badge-${badge}`}>{label}</span>;
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

interface FieldProps {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
  children: (describedBy: string | undefined, invalid: boolean) => ReactNode;
}

/** Connects the label, hint and error to the input for screen readers. */
export function Field({ id, label, hint, error, children }: FieldProps) {
  const ids = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean);
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children(ids.length > 0 ? ids.join(" ") : undefined, Boolean(error))}
      {hint ? (
        <span id={`${id}-hint`} className="field-hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={`${id}-error`} className="field-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

interface SummaryLabels {
  subtotal: string;
  discount: string;
  fee: string;
  total: string;
  vat: string;
}

export function PriceSummary({ quote, labels }: { quote: Quote; labels: SummaryLabels }) {
  const rows: [string, number, string?][] = [
    [labels.subtotal, quote.subtotal_satang],
    [labels.discount, -quote.discount_satang],
    [labels.fee, quote.fee_satang],
    [labels.total, quote.total_satang, "total"],
    [labels.vat, quote.vat_satang],
  ];
  return (
    <dl className="sum">
      {rows.map(([label, value, className]) => (
        <div key={label} className={className}>
          <dt>{label}</dt>
          <dd>{value < 0 ? `-฿${formatBaht(-value)}` : `฿${formatBaht(value)}`}</dd>
        </div>
      ))}
    </dl>
  );
}
