// Money is an integer number of satang everywhere. These helpers work on digits, never on
// fractional numbers, so no floating-point value can be involved.

export function formatBaht(satang: number): string {
  if (!Number.isSafeInteger(satang)) throw new Error("satang must be an integer");
  const negative = satang < 0;
  const digits = String(Math.abs(satang)).padStart(3, "0");
  const whole = digits.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${whole}.${digits.slice(-2)}`;
}

/** Plain decimal price for structured data, for example 34900 becomes "349.00". */
export function decimalPrice(satang: number): string {
  return formatBaht(satang).replaceAll(",", "");
}

/** Parses text such as "12.50" into satang. Returns null if it is not a valid amount. */
export function parseBaht(text: string): number | null {
  const match = /^([0-9]{1,10})(?:\.([0-9]{1,2}))?$/.exec(text.trim());
  if (match === null) return null;
  const fraction = (match[2] ?? "").padEnd(2, "0");
  return Number(`${match[1]}${fraction}`);
}
