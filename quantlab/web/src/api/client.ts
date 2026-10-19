import type { components } from "./schema";

type S = components["schemas"];
export type BacktestRequest = S["BacktestRequest"];
export type BacktestResponse = S["BacktestResponse"];
export type Summary = S["Summary"];
export type ScanRequest = S["ScanRequest"];
export type ScanResponse = S["ScanResponse"];
export type ScanRow = S["ScanRow"];
export type SignalItem = S["SignalItem"];
export type Account = S["Account"];
export type AccountCreate = S["AccountCreate"];
export type RecordResult = S["RecordResult"];
export type AiStatus = S["AiStatus"];
export type AiRun = S["AiRun"];
export type Study = S["Study"];
export type StudyRequest = S["StudyRequest"];
export type MlSignal = S["Signal"];
export type TradingAccount = S["TradingAccount"];
export type Limits = S["Limits"];

const TOKEN_KEY = "quantlab.token";
let memoryToken: string | null = null; // fallback when session storage is blocked

/** Takes the token from the start-up link (?token=...) into session storage and removes it from the address bar. */
export function adoptTokenFromUrl(): void {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  setToken(token);
  url.searchParams.delete("token");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token.trim());
  } catch {
    // storage blocked: the token lasts until the page is reloaded
    memoryToken = token.trim();
  }
}


export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type Detail = string | { msg: string; loc?: (string | number)[] }[];

/** Turns FastAPI's error body (a message, or a list of validation errors) into one readable sentence. */
export function errorMessage(detail: Detail | undefined, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d.msg.replace(/^Value error, /, "")).join("；");
  }
  return status === 0 ? "连不上服务，请确认 quantlab serve 正在运行" : `请求失败（${status}）`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError(errorMessage(undefined, 0), 0);
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorMessage(body.detail, res.status), res.status);
  return body as T;
}

export const post = <T>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
