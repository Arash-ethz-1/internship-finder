/**
 * The only module that knows about API URLs.
 *
 * Every response type here mirrors a Pydantic schema in `api/schemas.py`.
 * Phase 7 defines those schemas and Phase 8 adds the rest of these calls;
 * for now there is one endpoint, enough to prove the two servers talk.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return (await response.json()) as T;
}

export interface Health {
  status: string;
  version: string;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/api/health");
}
