import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import type { UploadResult } from "../../api/types";
import { fakeFile } from "../../test/fakeDrop";
import { fakeServer } from "../../test/fakeServer";
import { id } from "../../test/records";
import { NoticeProvider } from "../shell/Notices";
import { useCaseUpload } from "./useCaseUpload";

const wrapper = ({ children }: { children: ReactNode }) => (
  <NoticeProvider>
    <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
  </NoticeProvider>
);

const photos = (prefix: string, count: number) =>
  Array.from({ length: count }, (_, i) => fakeFile(`${prefix}${String(i)}.HEIC`));

describe("useCaseUpload", () => {
  it("queues overlapping drops: three requests at most, one notice per drop", async () => {
    let inFlight = 0;
    let peak = 0;
    fakeServer({
      "POST /api/cases/:case_id/photos": async (): Promise<UploadResult> => {
        inFlight++;
        peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 5));
        inFlight--;
        return { job_id: null, added: [id(2)], skipped: [] };
      },
    });
    const { result } = renderHook(() => useCaseUpload(id(1)), { wrapper });

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = result.current.upload([...photos("a", 5), fakeFile("a.MOV")]);
      second = result.current.upload([...photos("b", 4), fakeFile("b1.MOV"), fakeFile("b2.MOV")]);
    });
    expect(result.current.progress).toEqual({ done: 0, total: 9 });

    await act(async () => {
      await first;
    });
    // The second drop is still queued, so the progress must not reset.
    expect(result.current.progress).not.toBeNull();
    expect(screen.getByText("Skipped 1 file: 1 video")).toBeInTheDocument();
    expect(screen.queryByText("Skipped 2 files: 2 videos")).toBeNull();

    await act(async () => {
      await second;
    });

    expect(peak).toBe(3);
    expect(result.current.progress).toBeNull();
    expect(screen.getByText("Skipped 2 files: 2 videos")).toBeInTheDocument();
  });

  it("adds the unreadable entries to the drop's one notice", async () => {
    fakeServer({
      "POST /api/cases/:case_id/photos": (): UploadResult => ({
        job_id: null,
        added: [id(2)],
        skipped: [],
      }),
    });
    const { result } = renderHook(() => useCaseUpload(id(1)), { wrapper });

    await act(async () => {
      await result.current.upload(photos("a", 1), [
        { name: "bad.HEIC", message: "could not be read" },
      ]);
    });

    expect(screen.getByRole("alert")).toHaveTextContent("bad.HEIC: could not be read");
  });
});
