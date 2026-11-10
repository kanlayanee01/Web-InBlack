"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Field, PriceSummary, Spinner } from "@/components/ui";
import { api, ApiError, randomKey } from "@/lib/api";
import { clearCart, readCart, setQuantity, useCart } from "@/lib/cart";
import { dict, errorMessage, type Locale } from "@/lib/i18n";
import { orderLimit } from "@/lib/limits";
import { formatBaht } from "@/lib/money";
import type { Order, PaymentMethod, Product, Quote } from "@/lib/types";

const METHODS: PaymentMethod[] = ["promptpay", "truemoney", "card"];
const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;
const GAME_UID = /^[A-Za-z0-9_-]{4,32}$/;
const QUOTE_DEBOUNCE_MS = 250;
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
  const cart = useCart();
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
  // A signed-in customer must send the CSRF token with every request that changes data.
  const csrf = useRef<string | null>(null);

  const inCart = useMemo(
    () => products.filter((product) => (cart[product.sku] ?? 0) > 0),
    [cart, products],
  );
  const items = useMemo(
    () => inCart.map((product) => ({ sku: product.sku, qty: cart[product.sku] })),
    [cart, inCart],
  );

  // "Buy now" links arrive with ?sku=; the item joins the cart once, so a reload adds nothing.
  useEffect(() => {
    const known = preselected !== null && products.some((p) => p.sku === preselected);
    if (known && !(preselected in readCart())) setQuantity(preselected, 1);
  }, [preselected, products]);

  // A signed-in customer does not have to type their email again.
  useEffect(() => {
    api<{ email: string; csrf_token: string }>("/api/v1/auth/me")
      .then((me) => {
        csrf.current = me.csrf_token;
        setEmail((current) => current || me.email);
      })
      .catch(() => undefined);
  }, []);

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
    // Never drops to zero while typing; removing an item is the remove button's job.
    const parsed = /^[0-9]{1,2}$/.test(text) ? Number(text) : 1;
    setQuantity(sku, Math.max(1, Math.min(parsed, limit)));
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
      const key = attempt.current.key;
      const send = () =>
        api<Order>("/api/v1/orders", {
          method: "POST",
          body,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            ...(csrf.current ? { "X-CSRF-Token": csrf.current } : {}),
          },
        });
      let order: Order;
      try {
        order = await send();
      } catch (error) {
        // Submitted before the session lookup finished: fetch the token once, then retry.
        if (!(error instanceof ApiError && error.code === "CSRF_FAILED")) throw error;
        csrf.current = (await api<{ csrf_token: string }>("/api/v1/auth/me")).csrf_token;
        order = await send();
      }
      sessionStorage.setItem(`ib:order:${order.id}`, JSON.stringify(order));
      clearCart();
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
            {inCart.length === 0 ? (
              <div className="stack">
                <p className="muted">{t.cart.empty}</p>
                <Link className="btn btn-secondary" href={`/${locale}`}>
                  {t.cart.browse}
                </Link>
              </div>
            ) : (
              inCart.map((product) => {
                const qty = cart[product.sku];
                const limit = orderLimit(product);
                const perEmail =
                  product.max_qty_per_email !== null
                    ? ` · ${t.product.perEmail(product.max_qty_per_email)}`
                    : "";
                return (
                  <div key={product.sku} className="cart-line">
                    <div className="cart-info">
                      <span className="strong">{product.name}</span>
                      <span className="field-hint">
                        ฿{formatBaht(product.price_satang)}
                        {perEmail}
                      </span>
                    </div>
                    <div className="qty">
                      <button
                        type="button"
                        className="qbtn"
                        aria-label={`${t.cart.decrease} ${product.name}`}
                        disabled={qty <= 1}
                        onClick={() => setQuantity(product.sku, qty - 1)}
                      >
                        −
                      </button>
                      <input
                        id={`qty-${product.sku}`}
                        className="input qty-input"
                        inputMode="numeric"
                        aria-label={`${t.checkout.quantity} ${product.name}`}
                        value={qty}
                        onChange={(event) => setQty(product.sku, event.target.value, limit)}
                      />
                      <button
                        type="button"
                        className="qbtn"
                        aria-label={`${t.cart.increase} ${product.name}`}
                        disabled={qty >= limit}
                        onClick={() => setQuantity(product.sku, qty + 1)}
                      >
                        +
                      </button>
                    </div>
                    <button
                      type="button"
                      className="link-btn"
                      aria-label={`${t.cart.remove} ${product.name}`}
                      onClick={() => setQuantity(product.sku, 0)}
                    >
                      {t.cart.remove}
                    </button>
                  </div>
                );
              })
            )}
            {inCart.length > 0 ? (
              <Link className="small" href={`/${locale}`}>
                {t.cart.addMore}
              </Link>
            ) : null}
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
