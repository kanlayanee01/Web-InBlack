"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Field, PriceSummary, Spinner } from "@/components/ui";
import { api, ApiError, randomKey } from "@/lib/api";
import { dict, errorMessage, type Locale } from "@/lib/i18n";
import { formatBaht } from "@/lib/money";
import type { Order, PaymentMethod, Product, Quote } from "@/lib/types";

const METHODS: PaymentMethod[] = ["promptpay", "truemoney", "card"];
const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;
const GAME_UID = /^[A-Za-z0-9_-]{4,32}$/;
const QUOTE_DEBOUNCE_MS = 250;
const MAX_QTY = 99;
const VOUCHER_ERRORS = ["VOUCHER_NOT_FOUND", "VOUCHER_MIN_SPEND"];

interface Errors {
  email?: string;
  gameUid?: string;
  voucher?: string;
  items?: string;
}

export function CheckoutForm({ locale, products }: { locale: Locale; products: Product[] }) {
  const t = dict(locale);
  const router = useRouter();
  const preselected = useSearchParams().get("sku");
  const [quantities, setQuantities] = useState<Record<string, number>>(() =>
    preselected !== null && products.some((p) => p.sku === preselected)
      ? { [preselected]: 1 }
      : {},
  );
  const [email, setEmail] = useState("");
  const [gameUid, setGameUid] = useState("");
  const [voucher, setVoucher] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("promptpay");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The same key is reused until the form changes, so a retry after a lost response is safe.
  const attempt = useRef<{ body: string; key: string } | null>(null);

  const items = useMemo(
    () =>
      Object.entries(quantities)
        .filter(([, qty]) => qty > 0)
        .map(([sku, qty]) => ({ sku, qty })),
    [quantities],
  );

  useEffect(() => {
    if (items.length === 0) {
      setQuote(null);
      return;
    }
    const controller = new AbortController();
    const fetchQuote = (voucher_code: string | null) =>
      api<Quote>("/api/v1/orders/quote", {
        method: "POST",
        json: { items, payment_method: method, voucher_code },
        signal: controller.signal,
      });
    const code = voucher.trim() || null;
    const timer = setTimeout(async () => {
      setQuoting(true);
      try {
        setQuote(await fetchQuote(code));
        setErrors((previous) => ({ ...previous, voucher: undefined }));
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && VOUCHER_ERRORS.includes(error.code)) {
          setErrors((previous) => ({ ...previous, voucher: errorMessage(locale, error.code) }));
          try {
            setQuote(await fetchQuote(null)); // still show a correct price without the voucher
          } catch {
            setQuote(null);
          }
        } else if (error instanceof ApiError) {
          setQuote(null);
          setFormError(errorMessage(locale, error.code));
        }
      } finally {
        if (!controller.signal.aborted) setQuoting(false);
      }
    }, QUOTE_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [items, method, voucher, locale]);

  function setQty(sku: string, text: string, limit: number) {
    const parsed = /^[0-9]{1,2}$/.test(text) ? Number(text) : 0;
    setQuantities((previous) => ({ ...previous, [sku]: Math.min(parsed, limit) }));
    setFormError(null);
  }

  function validate(): Errors {
    const found: Errors = {};
    if (items.length === 0) found.items = t.checkout.pickItem;
    if (!EMAIL.test(email.trim())) found.email = t.checkout.invalidEmail;
    if (!GAME_UID.test(gameUid.trim())) found.gameUid = t.checkout.invalidUid;
    return found;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    const found = validate();
    setErrors((previous) => ({ ...found, voucher: previous.voucher }));
    if (Object.keys(found).length > 0) {
      const firstInvalid = found.items ? "items" : found.email ? "email" : "game-uid";
      document.getElementById(firstInvalid)?.focus();
      return;
    }
    const body = JSON.stringify({
      items,
      email: email.trim(),
      game_uid: gameUid.trim(),
      payment_method: method,
      voucher_code: voucher.trim() || null,
    });
    if (attempt.current?.body !== body) attempt.current = { body, key: randomKey() };
    setSubmitting(true);
    try {
      const order = await api<Order>("/api/v1/orders", {
        method: "POST",
        body,
        headers: { "Content-Type": "application/json", "Idempotency-Key": attempt.current.key },
      });
      sessionStorage.setItem(`ib:order:${order.id}`, JSON.stringify(order));
      router.push(`/${locale}/track?order=${encodeURIComponent(order.id)}`);
    } catch (error) {
      setSubmitting(false);
      if (error instanceof ApiError && error.code.startsWith("VOUCHER")) {
        setErrors((previous) => ({ ...previous, voucher: errorMessage(locale, error.code) }));
        document.getElementById("voucher")?.focus();
      } else {
        setFormError(errorMessage(locale, error instanceof ApiError ? error.code : "generic"));
      }
    }
  }

  return (
    <form onSubmit={submit} noValidate>
      <h1>{t.checkout.title}</h1>
      <div className="checkout">
        <div className="stack">
          <section className="card" aria-labelledby="items-title" id="items" tabIndex={-1}>
            <h2 id="items-title">{t.checkout.items}</h2>
            {products.map((product) => {
              const limit = Math.min(
                product.max_qty_per_order ?? MAX_QTY,
                product.stock_remaining ?? MAX_QTY,
              );
              return (
                <div key={product.sku} className="line-item">
                  <label htmlFor={`qty-${product.sku}`}>
                    {product.name}
                    <span className="field-hint">฿{formatBaht(product.price_satang)}</span>
                  </label>
                  <input
                    id={`qty-${product.sku}`}
                    className="input"
                    inputMode="numeric"
                    aria-label={`${t.checkout.quantity} ${product.name}`}
                    value={quantities[product.sku] ?? 0}
                    onChange={(event) => setQty(product.sku, event.target.value, limit)}
                  />
                </div>
              );
            })}
            {errors.items ? <span className="field-error">{errors.items}</span> : null}
          </section>

          <section className="card stack">
            <Field
              id="email"
              label={t.checkout.email}
              hint={t.checkout.emailHint}
              error={errors.email}
            >
              {(describedBy, invalid) => (
                <input
                  id="email"
                  className="input"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </Field>
            <Field
              id="game-uid"
              label={t.checkout.gameUid}
              hint={t.checkout.gameUidHint}
              error={errors.gameUid}
            >
              {(describedBy, invalid) => (
                <input
                  id="game-uid"
                  className="input"
                  required
                  autoComplete="off"
                  value={gameUid}
                  onChange={(event) => setGameUid(event.target.value)}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </Field>
            <Field id="voucher" label={t.checkout.voucher} error={errors.voucher}>
              {(describedBy, invalid) => (
                <input
                  id="voucher"
                  className="input"
                  autoComplete="off"
                  value={voucher}
                  onChange={(event) => setVoucher(event.target.value.toUpperCase())}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </Field>
            <fieldset>
              <legend>{t.checkout.payment}</legend>
              {METHODS.map((value) => (
                <label key={value} className="choice">
                  <input
                    type="radio"
                    name="method"
                    value={value}
                    checked={method === value}
                    onChange={() => setMethod(value)}
                  />
                  {t.methods[value]}
                </label>
              ))}
            </fieldset>
          </section>
        </div>

        <aside className="card stack" aria-labelledby="summary-title">
          <div className="row between">
            <h2 id="summary-title">{t.checkout.summary}</h2>
            {quoting ? (
              <span className="muted small row">
                <Spinner /> {t.checkout.calculating}
              </span>
            ) : null}
          </div>
          <div aria-live="polite" aria-atomic="true">
            {quote ? (
              <PriceSummary quote={quote} labels={t.checkout} />
            ) : (
              <p className="muted">{t.checkout.pickItem}</p>
            )}
          </div>
          {formError ? (
            <p className="alert" role="alert">
              {formError}
            </p>
          ) : null}
          <button className="btn btn-block" type="submit" disabled={submitting}>
            {submitting ? <Spinner /> : null}
            {submitting ? t.checkout.submitting : t.checkout.submit}
          </button>
        </aside>
      </div>
    </form>
  );
}
