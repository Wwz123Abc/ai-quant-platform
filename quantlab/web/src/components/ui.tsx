import type { ReactNode } from "react";
import { pct, tone } from "../lib/format";

/** A signed percentage, green for gains and red for losses; the sign is always shown too. */
export function Signed({ v, digits }: { v: number | null | undefined; digits?: number }) {
  return <span className={tone(v)}>{pct(v, digits)}</span>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Stat({ k, v, s }: { k: string; v: ReactNode; s?: ReactNode }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {s ? <div className="s">{s}</div> : null}
    </div>
  );
}

export function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={"field" + (wide ? " wide" : "")}>
      <label>
        {label}
        {children}
      </label>
    </div>
  );
}

export function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {children ? <p>{children}</p> : null}
    </div>
  );
}
