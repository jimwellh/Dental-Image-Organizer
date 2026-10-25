import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

/** A fresh client per call, with retries off so a failing request fails the test at once. */
export function renderWithClient(ui: ReactElement): RenderResult & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // As the `wrapper`, so `rerender` keeps the provider.
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return Object.assign(render(ui, { wrapper }), { client });
}
