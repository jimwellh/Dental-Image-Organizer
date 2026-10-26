import { QueryClientProvider, useMutation, type QueryClient } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeServer } from "../test/fakeServer";
import { id, photoRecord, procedureView } from "../test/records";
import { renderWithClient } from "../test/render";
import {
  createQueryClient,
  hasPending,
  PENDING_POLL_MS,
  queryKeys,
  useCase,
  useCaseChange,
  useTeachSession,
} from "./queries";
import type { CaseView } from "./types";

function withClient(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderQueryHook<T>(hook: () => T) {
  // The client of `renderWithClient`, so every test shares one definition of "a test client".
  const { client } = renderWithClient(<></>);
  return { client, ...renderHook(hook, { wrapper: withClient(client) }) };
}

const pending = procedureView([photoRecord({ state: "pending" })]);
const ready = procedureView([photoRecord()]);

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

describe("hasPending", () => {
  it("is true when any photo is pending", () => {
    expect(
      hasPending(procedureView([photoRecord(), photoRecord({ id: id(2), state: "pending" })])),
    ).toBe(true);
    expect(hasPending(ready)).toBe(false);
  });
});

describe("useCase", () => {
  it("keeps refreshing while a photo is pending, then stops", async () => {
    let replies = 0;
    const server = fakeServer({
      "GET /api/cases/:case_id": () => (replies++ === 0 ? pending : ready),
    });
    const { result } = renderQueryHook(() => useCase(id(100)));

    await waitFor(() => {
      expect(result.current.data).toEqual(pending);
    });
    expect(server.calls).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(PENDING_POLL_MS));
    await waitFor(() => {
      expect(result.current.data).toEqual(ready);
    });
    expect(server.calls).toHaveLength(2);

    await act(() => vi.advanceTimersByTimeAsync(4 * PENDING_POLL_MS + 1500));
    expect(server.calls).toHaveLength(2);
  });
});

describe("useTeachSession", () => {
  it("is disabled while there is no session", async () => {
    const server = fakeServer();
    const { result } = renderQueryHook(() => useTeachSession(null));
    await act(() => vi.advanceTimersByTimeAsync(PENDING_POLL_MS));
    expect(result.current.fetchStatus).toBe("idle");
    expect(server.calls).toHaveLength(0);
  });
});

describe("useCaseChange", () => {
  it("stores the returned view and refreshes the patient list", async () => {
    const { client, result } = renderQueryHook(() =>
      useCaseChange(id(100), (title: string) =>
        Promise.resolve(procedureView([], { case: { title } })),
      ),
    );
    client.setQueryData(queryKeys.patients, { patients: [], problems: [] });

    let stored: CaseView | undefined;
    await act(async () => {
      stored = await result.current.mutateAsync("Renamed");
    });

    expect(client.getQueryData(queryKeys.case(id(100)))).toBe(stored);
    expect(stored?.case.title).toBe("Renamed");
    expect(client.getQueryState(queryKeys.patients)?.isInvalidated).toBe(true);
  });

  it("keeps the changed view when a poll that started earlier resolves later", async () => {
    const caseId = id(100);
    const { client, result } = renderQueryHook(() =>
      useCaseChange(caseId, (title: string) =>
        Promise.resolve(procedureView([], { case: { title } })),
      ),
    );
    let finishPoll: (view: CaseView) => void = () => undefined;
    const poll = client
      .query({
        queryKey: queryKeys.case(caseId),
        queryFn: () =>
          new Promise<CaseView>((resolve) => {
            finishPoll = resolve;
          }),
      })
      .catch(() => undefined);

    await act(async () => {
      await result.current.mutateAsync("Renamed");
    });
    finishPoll(procedureView([], { case: { title: "Stale" } }));
    await poll;

    expect(client.getQueryData<CaseView>(queryKeys.case(caseId))?.case.title).toBe("Renamed");
  });
});

describe("createQueryClient", () => {
  async function fail(client: QueryClient, meta?: Record<string, unknown>) {
    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: () => Promise.reject(new Error("The server said no.")),
          meta,
        }),
      { wrapper: withClient(client) },
    );
    await act(async () => {
      await result.current.mutateAsync().catch(() => undefined);
    });
  }

  it("a failed mutation reaches the handler unless it is quiet", async () => {
    const handler = vi.fn<(error: unknown) => void>();
    const client = createQueryClient(handler);

    await fail(client);
    expect(handler).toHaveBeenCalledTimes(1);

    await fail(client, { quiet: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("refetches on window focus and does not retry", () => {
    const defaults = createQueryClient(vi.fn()).getDefaultOptions().queries;
    expect(defaults).toMatchObject({ retry: false, refetchOnWindowFocus: true });
  });
});
