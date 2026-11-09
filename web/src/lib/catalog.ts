import "server-only";

import type { Product } from "./types";

export const REVALIDATE_SECONDS = 30;
const API = process.env.API_INTERNAL_URL ?? "http://proxy";
const BUILDING = process.env.NEXT_PHASE === "phase-production-build";

// The API is not running during `docker build`, so pages are first generated from the seed data.
// ISR replaces them with live data within REVALIDATE_SECONDS of the stack starting.
const BUILD_CATALOG: Product[] = [
  entry("MONTHLY-PASS", "monthly-pass", "Monthly Pass", 17900, null, 1, null),
  entry("FLASH-SKIN-01", "flash-skin-01", "Limited Skin (Flash Sale)", 9900, 100, null, 2),
  entry("DIAMOND-1000", "diamond-1000", "1,000 Diamonds", 34900, null, null, null),
  entry("DIAMOND-5000", "diamond-5000", "5,000 Diamonds", 159000, null, null, null),
];

function entry(
  sku: string,
  slug: string,
  name: string,
  price: number,
  stock: number | null,
  perOrder: number | null,
  perEmail: number | null,
): Product {
  return {
    sku,
    slug,
    name,
    description_th: "",
    description_en: "",
    price_satang: price,
    stock_remaining: stock,
    max_qty_per_order: perOrder,
    max_qty_per_email: perEmail,
  };
}

export async function getProducts(): Promise<Product[]> {
  if (BUILDING) return BUILD_CATALOG;
  // A failure throws on purpose: ISR then keeps serving the last good page.
  const response = await fetch(`${API}/api/v1/products`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!response.ok) throw new Error(`catalog request failed: ${response.status}`);
  return ((await response.json()) as { items: Product[] }).items;
}

export async function getProduct(slug: string): Promise<Product | null> {
  if (BUILDING) return BUILD_CATALOG.find((product) => product.slug === slug) ?? null;
  const response = await fetch(`${API}/api/v1/products/${encodeURIComponent(slug)}`, {
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`product request failed: ${response.status}`);
  return (await response.json()) as Product;
}

export function buildSlugs(): string[] {
  return BUILD_CATALOG.map((product) => product.slug);
}

export function siteUrl(): string {
  return process.env.SITE_URL ?? "http://localhost:8088";
}
