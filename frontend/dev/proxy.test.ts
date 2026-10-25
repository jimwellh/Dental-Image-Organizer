import { apiProxy, DEV_ORIGINS } from "./proxy";

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

test.each([...DEV_ORIGINS])(
  "rewrites the dev server's origin %s to the backend's own",
  (origin) => {
    const request = fakeRequest({ origin });
    proxyReqHook()(request);
    expect(request.headers.origin).toBe(BACKEND);
  },
);

test("leaves a foreign origin for the backend to refuse", () => {
  const request = fakeRequest({ origin: "https://evil.example" });
  proxyReqHook()(request);
  expect(request.headers.origin).toBe("https://evil.example");
});

test("adds no origin to a request that had none", () => {
  const request = fakeRequest({});
  proxyReqHook()(request);
  expect(request.headers.origin).toBeUndefined();
});
