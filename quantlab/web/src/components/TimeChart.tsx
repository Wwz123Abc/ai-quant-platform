import {
  createChart,
  createSeriesMarkers,
  type IChartApi,
  LineSeries,
  type MouseEventParams,
  PriceScaleMode,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

export interface Line {
  name: string;
  /** A CSS custom property holding the colour, such as "--series-1". */
  color: string;
  values: (number | null | undefined)[];
}

export interface TradeMark {
  date: string;
  side: "buy" | "sell";
}

interface Props {
  dates: string[];
  lines: Line[];
  format: (v: number) => string;
  log?: boolean;
  /** Buy and sell arrows, drawn on the first line. */
  marks?: TradeMark[];
  size?: "short" | "tall";
  label: string;
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function useColorScheme(): "light" | "dark" {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark ? "dark" : "light";
}

/** Lines over trading days, with a legend that shows the values under the crosshair (or the latest ones). */
export default function TimeChart({ dates, lines, format, log, marks, size, label }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const scheme = useColorScheme();
  const [hover, setHover] = useState<number | null>(null);
  const indexOf = useMemo(() => new Map(dates.map((d, i) => [d, i])), [dates]);

  useEffect(() => {
    if (!box.current) return;
    const chart: IChartApi = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: css("--surface") }, textColor: css("--muted"), fontFamily: "inherit", fontSize: 12 },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--grid") } },
      rightPriceScale: { borderColor: css("--axis"), mode: log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
      timeScale: { borderColor: css("--axis") },
      crosshair: { vertLine: { labelBackgroundColor: css("--ink-2") }, horzLine: { labelBackgroundColor: css("--ink-2") } },
      localization: { locale: "zh-CN", priceFormatter: format },
    });
    const series = lines.map((line) => {
      const s = chart.addSeries(LineSeries, {
        color: css(line.color),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 4,
      });
      s.setData(dates.map((d, i) => {
        const v = line.values[i];
        return v == null || !Number.isFinite(v) ? { time: d as Time } : { time: d as Time, value: v };
      }));
      return s;
    });
    if (marks?.length && series[0]) {
      const up = css("--up");
      const down = css("--down");
      const ms: SeriesMarker<Time>[] = marks.map((m) => ({
        time: m.date as Time,
        position: m.side === "buy" ? "belowBar" : "aboveBar",
        shape: m.side === "buy" ? "arrowUp" : "arrowDown",
        color: m.side === "buy" ? up : down,
        text: m.side === "buy" ? "买" : "卖",
        size: 1,
      }));
      createSeriesMarkers(series[0], ms);
    }
    chart.timeScale().fitContent();
    const onMove = (p: MouseEventParams<Time>) => setHover(p.time ? (indexOf.get(String(p.time)) ?? null) : null);
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
    };
  }, [dates, lines, format, log, marks, scheme, indexOf]);

  const at = hover ?? dates.length - 1;
  return (
    <div>
      <div className="chart-head">
        <h2 style={{ margin: 0 }}>{label}</h2>
        <div className="legend" aria-live="polite">
          <span>{dates[at] ?? ""}</span>
          {lines.map((l) => {
            const v = l.values[at];
            return (
              <span key={l.name}>
                <span className="sw" style={{ background: `var(${l.color})` }} />
                {l.name} <b>{v == null || !Number.isFinite(v) ? "—" : format(v)}</b>
              </span>
            );
          })}
        </div>
      </div>
      <div ref={box} className={"chart" + (size ? " " + size : "")} role="img" aria-label={label} />
    </div>
  );
}
