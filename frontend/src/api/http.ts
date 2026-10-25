import { en } from "../strings/en";
import type { paths } from "./schema";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Fills the `{name}` parts of a route template and appends the query values that are defined.
 * The template is checked against the generated `paths`, so a renamed route fails to compile.
 */
export function apiPath(
  template: keyof paths,
  params: Record<string, string> = {},
  query: Record<string, string | undefined> = {},
): string {
  const path = template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const value = params[name];
    if (value === undefined) {
      throw new Error(`Missing path parameter "${name}" for ${template}.`);
    }
    return encodeURIComponent(value);
  });
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value !== undefined) search.append(name, value);
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}

async function errorFrom(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => undefined);
  if (isRecord(body) && isRecord(body.error)) {
    const { code, message, details } = body.error;
    if (typeof code === "string" && typeof message === "string") {
      return new ApiError(response.status, code, message, details);
    }
  }
  return new ApiError(response.status, `http_${String(response.status)}`, en.errors.unexpected);
}

// `T` is a promise about the server's reply: the generated OpenAPI types are checked against the
// backend's schema elsewhere, so the body is not re-validated on every call.
async function request<T>(method: string, url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { ...init, method });
  } catch {
    throw new ApiError(0, "network", en.errors.network);
  }
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(response.status, `http_${String(response.status)}`, en.errors.unexpected);
  }
}

export function getJson<T>(url: string): Promise<T> {
  return request<T>("GET", url);
}

export function sendJson<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  url: string,
  body?: unknown,
): Promise<T> {
  if (body === undefined) return request<T>(method, url);
  return request<T>(method, url, {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// No `Content-Type` here: only the browser knows the multipart boundary, and it also sets
// `Content-Length`, which the server's upload limit (§4.13) needs.
export function sendForm<T>(method: "POST" | "PUT", url: string, form: FormData): Promise<T> {
  return request<T>(method, url, { body: form });
}

/**
 * Each file goes under `field`, followed by one `last_modified` (ms) per file. The server falls
 * back to that time when a photo has no EXIF date (§4.4 step 5).
 */
export function formWithFiles(files: readonly File[], field = "files"): FormData {
  const form = new FormData();
  for (const file of files) form.append(field, file);
  for (const file of files) form.append("last_modified", String(file.lastModified));
  return form;
}

/** The text a notice shows for a failed request. */
export function describeError(error: unknown): string {
  if (!(error instanceof ApiError)) return en.errors.unexpected;
  if (error.code === "invalid_request" && Array.isArray(error.details)) {
    const first: unknown = error.details[0];
    if (isRecord(first) && Array.isArray(first.loc) && typeof first.msg === "string") {
      const field: unknown = first.loc.at(-1);
      if (typeof field === "string" || typeof field === "number") {
        return `${error.message} ${String(field)}: ${first.msg}`;
      }
    }
  }
  return error.message;
}
