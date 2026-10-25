import { apiProxy } from "./proxy";

const BACKEND = "http://127.0.0.1:8765";

function fakeRequest(headers: Record<string, string>) {
  return {
    headers: { ...headers } as Record<string, string | undefined>,
    getHeader(name: string) {
      return this.headers[name];
    },
    setHeader(name: string, value: string) {
      this.headers[name] = value;
    },
  };
}

function proxyReqHook() {
  const handlers: Record<string, (request: ReturnType<typeof fakeRequest>) => void> = {};
  const proxy = {
    on: (event: string, fn: (r: ReturnType<typeof fakeRequest>) => void) => {
      handlers[event] = fn;
    },
  };
  apiProxy(BACKEND).configure?.(proxy as never, {});
  const hook = handlers.proxyReq;
  if (!hook) throw new Error("apiProxy registered no proxyReq handler");
  return hook;
}

test("targets the backend and rewrites Host", () => {
  expect(apiProxy(BACKEND)).toMatchObject({ target: BACKEND, changeOrigin: true });
});

test("rewrites the browser's origin to the backend's own", () => {
  const request = fakeRequest({ origin: "http://localhost:5173" });
  proxyReqHook()(request);
  expect(request.headers.origin).toBe(BACKEND);
});

test("adds no origin to a request that had none", () => {
  const request = fakeRequest({});
  proxyReqHook()(request);
  expect(request.headers.origin).toBeUndefined();
});
