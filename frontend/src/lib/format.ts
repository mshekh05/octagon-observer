export const pct = (p: number | null | undefined, digits = 0) =>
  p == null ? "–" : `${(p * 100).toFixed(digits)}%`;

export const pts = (e: number | null | undefined) =>
  e == null ? "–" : `${e > 0 ? "+" : e < 0 ? "−" : ""}${Math.abs(e * 100).toFixed(1)} pts`;

export function american(decimal: number | null | undefined): string {
  if (!decimal || decimal <= 1) return "–";
  const a = decimal >= 2 ? Math.round((decimal - 1) * 100) : Math.round(-100 / (decimal - 1));
  return a > 0 ? `+${a}` : `−${Math.abs(a)}`;
}

export const surname = (name: string) => name.split(" ").slice(-1)[0];

/** US odds text ("+150", "-120") to a decimal price; null when it isn't a valid price. */
export function decimalFrom(text: string): number | null {
  const a = Number(text.replace("−", "-").replace("+", "").trim());
  if (!Number.isFinite(a) || Math.abs(a) < 100) return null;
  return a > 0 ? 1 + a / 100 : 1 + 100 / -a;
}

/** "−160 or better" style threshold text for a decimal price. */
export const orBetter = (decimal: number | null | undefined) => (decimal ? `${american(decimal)} or better` : "no price works");

export const americanInt = (a: number) => (a > 0 ? `+${a}` : `−${Math.abs(a)}`);

export const money = (n: number | null | undefined) =>
  n == null ? "–" : `${n < 0 ? "−" : ""}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
