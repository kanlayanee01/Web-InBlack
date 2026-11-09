import assert from "node:assert/strict";
import { test } from "node:test";

import { decimalPrice, formatBaht, parseBaht } from "../src/lib/money.ts";
import { isValidOrderNumber } from "../src/lib/order-number.ts";

test("formats satang without touching fractions", () => {
  assert.equal(formatBaht(0), "0.00");
  assert.equal(formatBaht(5), "0.05");
  assert.equal(formatBaht(66980), "669.80");
  assert.equal(formatBaht(159000), "1,590.00");
  assert.equal(formatBaht(123456789), "1,234,567.89");
  assert.equal(formatBaht(-5000), "-50.00");
});

test("rejects a non-integer amount", () => {
  assert.throws(() => formatBaht(349.5));
});

test("gives a plain decimal for structured data", () => {
  assert.equal(decimalPrice(159000), "1590.00");
});

test("parses baht text into exact satang", () => {
  assert.equal(parseBaht("10.50"), 1050);
  assert.equal(parseBaht("0.07"), 7);
  assert.equal(parseBaht("1.1"), 110);
  assert.equal(parseBaht("19.99"), 1999); // 19.99 * 100 is 1998.9999... in floating point
  assert.equal(parseBaht("1.234"), null);
  assert.equal(parseBaht("-1"), null);
  assert.equal(parseBaht("abc"), null);
});

test("checks the order number checksum like the backend", () => {
  assert.equal(isValidOrderNumber("ORD-2587965432159-229"), true);
  assert.equal(isValidOrderNumber("ORD-2587965432159-458"), false);
  assert.equal(isValidOrderNumber("ORD-258796543215-229"), false);
  assert.equal(isValidOrderNumber("ord-2587965432159-229"), false);
});
