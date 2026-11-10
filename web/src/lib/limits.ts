import type { Product } from "./types";

const MAX_QTY = 99;

/** The most of one product a single order can hold, from the per-order cap and the stock left. */
export function orderLimit(product: Product): number {
  return Math.min(product.max_qty_per_order ?? MAX_QTY, product.stock_remaining ?? MAX_QTY);
}
