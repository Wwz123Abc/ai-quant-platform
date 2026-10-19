interface Item {
  label: string;
  value: number | null | undefined;
}

interface Props {
  items: Item[];
  format: (v: number) => string;
  /** "signed": a zero axis, gains green and losses red. "plain": one colour from zero. */
  kind?: "signed" | "plain";
  label: string;
}

/** Horizontal bars with the value printed beside each, so the numbers never depend on colour. */
export default function Bars({ items, format, kind = "plain", label }: Props) {
  const vals = items.map((i) => i.value ?? 0);
  const max = Math.max(1e-12, ...vals.map(Math.abs));
  const signed = kind === "signed" && vals.some((v) => v < 0);
  return (
    <div className="bars" role="table" aria-label={label}>
      {items.map((it) => {
        const v = it.value ?? 0;
        const share = Math.abs(v) / max;
        const left = signed ? (v < 0 ? 50 - share * 50 : 50) : 0;
        const width = signed ? share * 50 : share * 100;
        const color = kind === "signed" ? (v >= 0 ? "var(--up)" : "var(--down)") : "var(--series-1)";
        return [
          <span key={it.label + "l"} className="hint">{it.label}</span>,
          <span key={it.label + "t"} className="track">
            {signed ? <span className="axis" style={{ left: "50%" }} /> : null}
            <span className="bar" style={{ left: `${left}%`, width: `${width}%`, background: color }} />
          </span>,
          <span key={it.label + "v"} className={kind === "signed" ? (v > 0 ? "up" : v < 0 ? "down" : "flat") : undefined}>
            {it.value == null ? "—" : format(v)}
          </span>,
        ];
      })}
    </div>
  );
}
