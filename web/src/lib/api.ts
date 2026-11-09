export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    detail: string,
    readonly retryAfter: string | null,
  ) {
    super(detail);
  }
}

export interface RequestOptions {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...options.headers };
  if (options.json !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers,
    body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
    signal: options.signal,
    cache: "no-store",
    credentials: "same-origin",
  });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let parsed: { code?: string; detail?: string } = {};
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    // A proxy error page is not JSON. It is handled by the status check below.
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      parsed.code ?? "UNAVAILABLE",
      parsed.detail ?? "Request failed",
      response.headers.get("Retry-After"),
    );
  }
  return parsed as T;
}

export function randomKey(): string {
  // getRandomValues works on plain http too, unlike crypto.randomUUID.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
