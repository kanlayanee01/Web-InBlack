// BigInt keeps the modulo exact. Thirteen digits fit in a double, but BigInt leaves no doubt.
export function isValidOrderNumber(value: string): boolean {
  const match = /^ORD-([0-9]{13})-([0-9]{3})$/.exec(value);
  if (match === null) return false;
  return (BigInt(match[1]) % 997n).toString().padStart(3, "0") === match[2];
}
