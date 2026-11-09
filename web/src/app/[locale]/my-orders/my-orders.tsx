"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Field, Spinner, StatusBadge } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { dict, errorMessage, type Locale } from "@/lib/i18n";
import { formatBaht } from "@/lib/money";
import type { OrderSummary } from "@/lib/types";

interface Session {
  email: string;
  csrf_token: string;
}

type Tab = "current" | "past";

export function MyOrders({ locale }: { locale: Locale }) {
  const t = dict(locale);
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    api<Session>("/api/v1/auth/me")
      .then(setSession)
      .catch(() => undefined)
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <div className="skeleton" aria-busy="true" />;
  if (session === null) return <SignIn locale={locale} onSignedIn={setSession} />;
  return (
    <div className="stack">
      <div className="row between">
        <h1 style={{ marginBottom: 0 }}>{t.my.title}</h1>
        <button
          className="btn btn-secondary"
          type="button"
          onClick={async () => {
            await api("/api/v1/auth/logout", {
              method: "POST",
              headers: { "X-CSRF-Token": session.csrf_token },
            });
            setSession(null);
          }}
        >
          {t.my.logout}
        </button>
      </div>
      <OrderList locale={locale} />
    </div>
  );
}

function SignIn({
  locale,
  onSignedIn,
}: {
  locale: Locale;
  onSignedIn: (session: Session) => void;
}) {
  const t = dict(locale);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function explain(failure: unknown): string {
    if (failure instanceof ApiError && failure.code === "OTP_INVALID") return t.my.invalidCode;
    if (failure instanceof ApiError && failure.code === "OTP_LOCKED") return t.my.locked;
    if (failure instanceof ApiError && failure.status === 429) {
      const rateLimited = t.errors.rateLimited as (seconds: string) => string;
      return rateLimited(failure.retryAfter ?? "60");
    }
    return errorMessage(locale, "generic");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (!sent) {
        await api("/api/v1/auth/otp/request", { method: "POST", json: { email: email.trim() } });
        setSent(true);
      } else {
        onSignedIn(
          await api<Session>("/api/v1/auth/otp/verify", {
            method: "POST",
            json: { email: email.trim(), code: code.trim() },
          }),
        );
      }
    } catch (failure) {
      setError(explain(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate>
      <h1>{t.my.title}</h1>
      <Field id="otp-email" label={t.my.email}>
        {(describedBy) => (
          <input
            id="otp-email"
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            readOnly={sent}
            onChange={(event) => setEmail(event.target.value)}
            aria-describedby={describedBy}
          />
        )}
      </Field>
      {sent ? (
        <Field id="otp-code" label={t.my.code} hint={t.my.codeHint} error={error}>
          {(describedBy, invalid) => (
            <input
              id="otp-code"
              className="input"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              aria-describedby={describedBy}
              aria-invalid={invalid}
            />
          )}
        </Field>
      ) : error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      <button className="btn" type="submit" disabled={busy}>
        {busy ? <Spinner /> : null}
        {sent ? (busy ? t.my.verifying : t.my.verify) : busy ? t.my.sending : t.my.sendCode}
      </button>
    </form>
  );
}

function OrderList({ locale }: { locale: Locale }) {
  const t = dict(locale);
  const [tab, setTab] = useState<Tab>("current");
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (selected: Tab) => {
    setOrders(null);
    setFailed(false);
    try {
      const result = await api<{ items: OrderSummary[] }>(`/api/v1/me/orders?tab=${selected}`);
      setOrders(result.items);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void load(tab);
  }, [tab, load]);

  return (
    <>
      <div className="tabs" role="tablist">
        {(["current", "past"] as Tab[]).map((value) => (
          <button
            key={value}
            className="btn btn-secondary tab"
            role="tab"
            type="button"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
          >
            {t.my[value]}
          </button>
        ))}
      </div>
      {failed ? (
        <p className="alert" role="alert">
          {errorMessage(locale, "generic")}
        </p>
      ) : orders === null ? (
        <div className="skeleton" aria-busy="true" />
      ) : orders.length === 0 ? (
        <p className="muted">{t.my.empty}</p>
      ) : (
        orders.map((order) => (
          <article key={order.id} className="card row between">
            <div>
              <Link href={`/${locale}/track?order=${encodeURIComponent(order.id)}`}>
                {order.id}
              </Link>
              <div className="muted small">฿{formatBaht(order.total_satang)}</div>
            </div>
            <StatusBadge badge={order.badge} label={t.badges[order.badge]} />
          </article>
        ))
      )}
    </>
  );
}
