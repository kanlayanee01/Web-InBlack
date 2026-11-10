"use client";

// The cart lives in the browser. Prices are never stored here: the server prices every order.
import { useSyncExternalStore } from "react";

export type Cart = Record<string, number>;

const KEY = "ib:cart";
const EVENT = "ib:cart";
const EMPTY = "{}";
const SERVER_CART: Cart = {};

// Used when storage is unavailable, so the cart still works for the current page.
let memory = EMPTY;
let cachedRaw: string | null = null;
let cached: Cart = {};

function readRaw(): string {
  try {
    return localStorage.getItem(KEY) ?? EMPTY;
  } catch {
    return memory;
  }
}

function snapshot(): Cart {
  const raw = readRaw();
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      const parsed: unknown = JSON.parse(raw);
      cached = parsed !== null && typeof parsed === "object" ? (parsed as Cart) : {};
    } catch {
      cached = {};
    }
  }
  return cached;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

export function useCart(): Cart {
  return useSyncExternalStore(subscribe, snapshot, () => SERVER_CART);
}

function writeCart(cart: Cart): void {
  const clean = Object.fromEntries(
    Object.entries(cart).filter(([, qty]) => Number.isSafeInteger(qty) && qty > 0),
  );
  const raw = JSON.stringify(clean);
  try {
    localStorage.setItem(KEY, raw);
  } catch {
    memory = raw;
  }
  window.dispatchEvent(new Event(EVENT));
}

export function readCart(): Cart {
  return snapshot();
}

export function setQuantity(sku: string, qty: number): void {
  writeCart({ ...snapshot(), [sku]: qty });
}

/** Adds one, capped at the limit. Returns the new quantity. */
export function addToCart(sku: string, limit: number): number {
  const next = Math.min((snapshot()[sku] ?? 0) + 1, limit);
  setQuantity(sku, next);
  return next;
}

export function clearCart(): void {
  writeCart({});
}

export function cartCount(cart: Cart): number {
  return Object.values(cart).reduce((sum, qty) => sum + qty, 0);
}
