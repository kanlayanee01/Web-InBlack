"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Field, PriceSummary, Spinner, StatusBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { dict, errorMessage, type Locale } from "@/lib/i18n";
import { formatBaht } from "@/lib/money";
import { isValidOrderNumber } from "@/lib/order-number";
import type { Order, TimelineEvent } from "@/lib/types";

function readStored(orderId: string): Order | null {
  try {
    const stored = sessionStorage.getItem(`ib:order:${orderId}`);
    return stored ? (JSON.parse(stored) as Order) : null;
  } catch {
    return null;
  }
}

export function TrackView({ locale }: { locale: Locale }) {
  const t = dict(locale);
  const fromUrl = useSearchParams().get("order") ?? "";
  const [orderId, setOrderId] = useState(fromUrl);
  const [email, setEmail] = useState("");
  const [idError, setIdError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [order, setOrder] = useState<Order | null>(null);
  const [loading, setLoading] = useState(fromUrl !== "");

  useEffect(() => {
    if (fromUrl === "") return;
    const stored = readStored(fromUrl);
    if (stored !== null) {
      setOrder(stored);
      setLoading(false);
      return;
    }
    if (!isValidOrderNumber(fromUrl)) {
      setLoading(false);
      return;
    }
    // Signed-in owners can open their order directly; everyone else gets the search form.
    api<Order>(`/api/v1/orders/${encodeURIComponent(fromUrl)}`)
      .then(setOrder)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [fromUrl]);

  async function search(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    const id = orderId.trim();
    if (!isValidOrderNumber(id)) {
      // Checked here first, so a mistyped number never reaches the API.
      setIdError(t.track.invalidId);
      document.getElementById("order-id")?.focus();
      return;
    }
    setIdError(null);
    setBusy(true);
    try {
      setOrder(
        await api<Order>("/api/v1/orders/track", {
          method: "POST",
          json: { order_id: id, email: email.trim() },
        }),
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) setFormError(t.track.notFound);
      else if (error instanceof ApiError && error.status === 429) {
        const rateLimited = t.errors.rateLimited as (seconds: string) => string;
        setFormError(rateLimited(error.retryAfter ?? "60"));
      } else {
        setFormError(errorMessage(locale, "generic"));
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="skeleton" aria-busy="true" />;
  if (order !== null) {
    return <OrderView key={order.id} locale={locale} order={order} onBack={() => setOrder(null)} />;
  }

  return (
    <form className="card stack" onSubmit={search} noValidate>
      <h1>{t.track.title}</h1>
      <Field id="order-id" label={t.track.orderId} hint={t.track.orderIdHint} error={idError}>
        {(describedBy, invalid) => (
          <input
            id="order-id"
            className="input"
            autoComplete="off"
            value={orderId}
            onChange={(event) => setOrderId(event.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      <Field id="track-email" label={t.track.email}>
        {(describedBy, invalid) => (
          <input
            id="track-email"
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-describedby={describedBy}
            aria-invalid={invalid}
          />
        )}
      </Field>
      {formError ? (
        <p className="alert" role="alert">
          {formError}
        </p>
      ) : null}
      <button className="btn" type="submit" disabled={busy}>
        {busy ? <Spinner /> : null}
        {busy ? t.track.searching : t.track.search}
      </button>
    </form>
  );
}

function OrderView({
  locale,
  order,
  onBack,
}: {
  locale: Locale;
  order: Order;
  onBack: () => void;
}) {
  const t = dict(locale);
  const [events, setEvents] = useState<TimelineEvent[]>(order.timeline ?? []);
  const [disconnected, setDisconnected] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    const url =
      `/api/v1/orders/${encodeURIComponent(order.id)}/events` +
      `?token=${encodeURIComponent(order.tracking_token)}`;
    // EventSource sends Last-Event-ID by itself when it reconnects.
    const source = new EventSource(url);
    source.addEventListener("open", () => setDisconnected(false));
    source.addEventListener("error", () => setDisconnected(true));
    source.addEventListener("status", (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as TimelineEvent;
      setEvents((previous) =>
        previous.some((known) => known.seq === event.seq)
          ? previous
          : [...previous, event].sort((a, b) => a.seq - b.seq),
      );
      setAnnouncement(t.track.statusChanged(t.badges[event.badge]));
    });
    return () => source.close();
  }, [order.id, order.tracking_token, t]);

  const latest = events.at(-1);
  const badge = latest?.badge ?? order.badge;
  const awaitingPayment = (latest?.status ?? order.status) === "PENDING_PAYMENT";
  const time = new Intl.DateTimeFormat(locale === "th" ? "th-TH" : "en-GB", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "Asia/Bangkok",
  });

  return (
    <div className="stack">
      <p className="sr-announcer" aria-live="polite" role="status" style={srOnly}>
        {announcement}
      </p>
      <section className="card stack">
        <div className="row between">
          <h1 style={{ marginBottom: 0 }}>{order.id}</h1>
          <StatusBadge badge={badge} label={t.badges[badge]} />
        </div>
        {disconnected ? <p className="notice">{t.track.reconnecting}</p> : null}
        {awaitingPayment ? (
          <>
            <p className="muted">
              {t.track.expiresAt} {time.format(new Date(order.expires_at))}
            </p>
            <a className="btn" href={order.checkout_url} target="_blank" rel="noopener">
              {t.track.pay}
            </a>
          </>
        ) : null}
      </section>
      <section className="card" aria-labelledby="timeline-title">
        <h2 id="timeline-title">{t.track.timeline}</h2>
        <ol className="timeline">
          {/* PAID and IN_QUEUE share a customer label, so repeats are shown once. */}
          {events.filter((event, index) => event.badge !== events[index - 1]?.badge).map((event) => (
            <li key={event.seq}>
              <strong>{t.badges[event.badge]}</strong>
              <span className="muted small"> {time.format(new Date(event.created_at))}</span>
            </li>
          ))}
        </ol>
      </section>
      <section className="card stack" aria-labelledby="order-items">
        <h2 id="order-items">{t.track.items}</h2>
        {order.items.map((item) => (
          <div key={item.sku} className="row between">
            <span>
              {item.name} x{item.qty}
            </span>
            <span>฿{formatBaht(item.unit_price_satang)}</span>
          </div>
        ))}
        <p className="muted small">
          {t.track.gameUid}: {order.game_uid}
        </p>
        <PriceSummary quote={order} labels={t.checkout} />
      </section>
      <button className="btn btn-secondary" type="button" onClick={onBack}>
        {t.track.another}
      </button>
    </div>
  );
}

const srOnly = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clipPath: "inset(50%)",
  whiteSpace: "nowrap",
} as const;
