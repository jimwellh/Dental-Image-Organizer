import { vi } from "vitest";

export interface FakeRequest {
  method: string;
  path: string;
  params: Record<string, string>;
  query: URLSearchParams;
  /** The parsed JSON, the `FormData`, or `undefined`. */
  body: unknown;
}

/**
 * A returned `Response` is sent as is, `undefined` becomes a 204, anything else is sent as JSON.
 * A handler that throws makes `fetch` reject, which is how a test takes the server down.
 */
export type Handler = (request: FakeRequest) => unknown;

export interface FakeServer {
  /** Every request, matched or not, in the order it arrived. */
  calls: FakeRequest[];
  /** `"METHOD /path"` of each request that no route matched. */
  unmatched: string[];
  /** Adds a route, or replaces the handler of the same route key. */
  on: (route: string, handler: Handler) => void;
}

interface Route {
  method: string;
  pattern: RegExp;
  names: string[];
  handler: Handler;
}

let lastServer: FakeServer | undefined;

function compile(route: string, handler: Handler): Route {
  const [method, template] = route.split(" ");
  if (method === undefined || template === undefined) {
    throw new Error(`A route is "METHOD /path", not "${route}".`);
  }
  const names: string[] = [];
  const source = template
    .split("/")
    .map((segment) => {
      if (!segment.startsWith(":")) return segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      names.push(segment.slice(1));
      return "([^/]+)";
    })
    .join("/");
  return { method, pattern: new RegExp(`^${source}$`), names, handler };
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function bodyOf(init: RequestInit | undefined): unknown {
  const body = init?.body;
  if (typeof body === "string") return JSON.parse(body) as unknown;
  return body instanceof FormData ? body : undefined;
}

export function errorReply(
  status: number,
  code: string,
  message: string,
  details?: unknown,
): Response {
  return new Response(JSON.stringify({ error: { code, message, details: details ?? null } }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function reply(result: unknown): Response {
  if (result instanceof Response) return result;
  if (result === undefined) return new Response(null, { status: 204 });
  return new Response(JSON.stringify(result), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Stubs the global `fetch` with an in-memory server; `setup.ts` unstubs it after each test. */
export function fakeServer(routes: Record<string, Handler> = {}): FakeServer {
  const table = new Map<string, Route>();
  const server: FakeServer = {
    calls: [],
    unmatched: [],
    on: (route, handler) => {
      table.set(route, compile(route, handler));
    },
  };
  for (const [route, handler] of Object.entries(routes)) server.on(route, handler);

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(urlOf(input), "http://localhost");
      const method = (init?.method ?? "GET").toUpperCase();
      const request: FakeRequest = {
        method,
        path: url.pathname,
        params: {},
        query: url.searchParams,
        body: bodyOf(init),
      };
      server.calls.push(request);
      for (const route of table.values()) {
        const match = route.method === method ? route.pattern.exec(url.pathname) : null;
        if (!match) continue;
        route.names.forEach((name, index) => {
          request.params[name] = decodeURIComponent(match[index + 1] ?? "");
        });
        return reply(await route.handler(request));
      }
      server.unmatched.push(`${method} ${url.pathname}`);
      return errorReply(404, "unmatched", `No fake route for ${method} ${url.pathname}.`);
    }),
  );
  lastServer = server;
  return server;
}

/**
 * Called by `setup.ts` after each test: a request nobody answered is a missing route in the test,
 * and the 404 it got would otherwise look like an app error.
 */
export function assertNoUnmatchedRequests(): void {
  const unmatched = lastServer?.unmatched ?? [];
  lastServer = undefined;
  if (unmatched.length > 0) {
    throw new Error(`The fake server had no route for: ${unmatched.join(", ")}`);
  }
}
