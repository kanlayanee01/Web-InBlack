"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { CartIcon } from "./icons";
import { Toast } from "./toast";
import { addToCart, cartCount, useCart } from "@/lib/cart";

interface AddProps {
  sku: string;
  name: string;
  limit: number;
  className: string;
  labels: { add: string; added: string; limitReached: string; soldOut: string };
}

export function AddToCart({ sku, name, limit, className, labels }: AddProps) {
  const cart = useCart();
  const [message, setMessage] = useState<string | null>(null);
  const close = useCallback(() => setMessage(null), []);
  const inCart = cart[sku] ?? 0;
  const soldOut = limit <= 0;
  const full = !soldOut && inCart >= limit;

  return (
    <>
      <button
        type="button"
        className={className}
        disabled={soldOut || full}
        aria-label={`${labels.add} ${name}`}
        onClick={() => {
          addToCart(sku, limit);
          setMessage(`${labels.added}: ${name}`);
        }}
      >
        {soldOut ? labels.soldOut : full ? labels.limitReached : labels.add}
      </button>
      <Toast message={message} onClose={close} />
    </>
  );
}

export function CartLink({ href, label }: { href: string; label: string }) {
  const count = cartCount(useCart());
  return (
    // The spoken name must contain the visible text (WCAG 2.5.3), so both read "ตะกร้า 2".
    <Link href={href} aria-label={count > 0 ? `${label} ${count}` : label} className="cart-link">
      <CartIcon />
      <span className="nav-text">{label}</span>
      {count > 0 ? <span className="cart-count">{` ${count}`}</span> : null}
    </Link>
  );
}
