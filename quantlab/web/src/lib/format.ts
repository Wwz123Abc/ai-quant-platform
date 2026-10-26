const MINUS = "−";

/** +12.3% / −4.5% / — ; the sign carries the direction so colour never does it alone. */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = (Math.abs(v) * 100).toFixed(digits);
  if (Number(s) === 0) return `${s}%`;
  return (v > 0 ? "+" : MINUS) + s + "%";
}

/** An unsigned percentage, for things like volatility or time in the market. */
export function pctPlain(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v) ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function money(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (v < 0 ? MINUS : "") + "$" + s;
}

export function num(v: number | null | undefined, digits = 2): string {
  return v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits).replace("-", MINUS);
}

/** "up", "down" or "flat", for colouring a figure green or red (gains green, losses red). */
export function tone(v: number | null | undefined): "up" | "down" | "flat" {
  if (v == null || !Number.isFinite(v) || Math.abs(v) < 1e-9) return "flat";
  return v > 0 ? "up" : "down";
}

/** Peak-to-trough drawdown of an equity curve, as negative fractions. */
export function drawdown(values: number[]): number[] {
  let peak = -Infinity;
  return values.map((v) => {
    peak = Math.max(peak, v);
    return peak > 0 ? v / peak - 1 : 0;
  });
}

/** "5,10, 20" -> [5, 10, 20]; anything that is not a whole number is dropped. */
export function parseInts(text: string): number[] {
  return text
    .split(/[,，\s]+/)
    .map((x) => Number(x))
    .filter((x) => Number.isInteger(x) && x > 0);
}

/** Large dollar amounts in 亿 (hundred millions): $3,030 亿. */
export function big(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return Math.abs(v) >= 1e8 ? `${v < 0 ? MINUS : ""}$${Math.round(Math.abs(v) / 1e8).toLocaleString("en-US")} 亿` : money(v);
}

const BEIJING = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

/** A server timestamp (UTC, ISO 8601) as Beijing time, "2026-10-23 16:24"; "—" when missing or unreadable. */
export function when(iso: string | null | undefined): string {
  const t = iso ? Date.parse(/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : iso + "Z") : Number.NaN;
  if (!Number.isFinite(t)) return "—";
  const p = Object.fromEntries(BEIJING.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
}
