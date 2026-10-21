import type { ScanRow } from "../api/client";

interface Props {
  rows: ScanRow[];
  value: (r: ScanRow) => number | null;
  rank: (r: ScanRow) => number;
  format: (v: number) => string;
  /** Shared across the panels being compared, so equal colours mean equal values. */
  domain: [number, number];
  best: { fast: number; slow: number };
  title: string;
  subtitle: string;
}

/** Fast-by-slow grid on a single-hue scale, light (low) to dark (high), with the value printed in each cell. */
export default function Heatmap({ rows, value, rank, format, domain, best, title, subtitle }: Props) {
  const fasts = [...new Set(rows.map((r) => r.fast))].sort((a, b) => a - b);
  const slows = [...new Set(rows.map((r) => r.slow))].sort((a, b) => a - b);
  const byKey = new Map(rows.map((r) => [`${r.fast}/${r.slow}`, r]));
  const [lo, hi] = domain;
  const n = rows.length;
  return (
    <div>
      <h2 style={{ marginBottom: 2 }}>{title}</h2>
      <p className="hint">{subtitle}</p>
      <div className="heat" style={{ gridTemplateColumns: `44px repeat(${slows.length}, minmax(34px, 1fr))` }} role="table" aria-label={title}>
        <span className="lab">短\长</span>
        {slows.map((s) => (
          <span key={s} className="lab">{s}</span>
        ))}
        {fasts.flatMap((f) => [
          <span key={`l${f}`} className="lab">{f}</span>,
          ...slows.map((s) => {
          const r = byKey.get(`${f}/${s}`);
          const v = r ? value(r) : null;
          if (!r || v == null) return <span key={`${f}/${s}`} className="cell none" />;
          const t = hi > lo ? Math.min(1, Math.max(0, (v - lo) / (hi - lo))) : 0.5;
          const isBest = r.fast === best.fast && r.slow === best.slow;
          const tip = `${f}/${s}：${format(v)}，第 ${rank(r)} / ${n} 名`;
          return (
            <span
              key={`${f}/${s}`}
              className={"cell" + (isBest ? " best" : "")}
              title={tip}
              aria-label={tip}
              style={{
                background: `color-mix(in oklab, var(--seq-1) ${Math.round(t * 100)}%, var(--seq-0))`,
                color: t > 0.55 ? "var(--seq-ink-hi)" : "var(--seq-ink-lo)",
              }}
            >
              {format(v)}
            </span>
          );
        }),
        ])}
      </div>
    </div>
  );
}
