import { createEvent, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { UploadResult } from "../../api/types";
import { fakeFile, fakeTransfer, fileEntry } from "../../test/fakeDrop";
import { fakeServer } from "../../test/fakeServer";
import { id } from "../../test/records";
import { renderWithClient } from "../../test/render";
import { en } from "../../strings/en";
import { DropZone } from "./DropZone";
import { useCaseUpload } from "./useCaseUpload";

const CASE_ID = id(1);

function Wired() {
  const { upload } = useCaseUpload(CASE_ID);
  return (
    <DropZone
      label="Photos"
      onFiles={(files, unreadable) => {
        void upload(files, unreadable);
      }}
    />
  );
}

describe("DropZone", () => {
  it("one drop, one summary", async () => {
    const server = fakeServer({
      "POST /api/cases/:case_id/photos": (request): UploadResult => {
        const form = request.body as FormData;
        const name = (form.get("files") as File).name;
        if (name === "B.HEIC") {
          return {
            job_id: null,
            added: [],
            skipped: [{ name, reason: "duplicate", message: "Already in the case." }],
          };
        }
        return { job_id: id(9), added: [id(2)], skipped: [] };
      },
    });
    renderWithClient(<Wired />);

    const entries = ["A.HEIC", "B.HEIC", "C.MOV", ".DS_Store"].map((n) => fileEntry(fakeFile(n)));
    fireEvent.drop(screen.getByRole("region", { name: "Photos" }), {
      dataTransfer: fakeTransfer(entries),
    });

    const notice = await screen.findByText("Skipped 2 files: 1 video, 1 already in the case");
    expect(notice).toBeInTheDocument();
    expect(server.calls.filter((call) => call.method === "POST")).toHaveLength(2);
    expect(screen.getAllByText(/^Skipped /)).toHaveLength(1);
  });

  it("the file button works without a drag", async () => {
    const onFiles = vi.fn();
    const { container } = renderWithClient(<DropZone label="Photos" onFiles={onFiles} />);
    const files = [fakeFile("one.HEIC"), fakeFile("two.JPG")];

    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    await userEvent.upload(input as HTMLInputElement, files);

    expect(onFiles).toHaveBeenCalledWith(files, []);
    expect(screen.getByRole("button", { name: en.intake.chooseFiles })).toBeInTheDocument();
    expect(screen.getByText(en.intake.photosHelp)).toBeInTheDocument();
  });

  it("passes the readable files on with the unreadable ones, without an alert", async () => {
    const onFiles = vi.fn();
    renderWithClient(<DropZone label="Photos" onFiles={onFiles} />);
    const broken = {
      isFile: true,
      isDirectory: false,
      name: "bad.HEIC",
      file: (_ok: unknown, fail: (error: Error) => void) => {
        fail(new Error("gone"));
      },
    } as unknown as FileSystemFileEntry;
    const good = fakeFile("good.HEIC");

    fireEvent.drop(screen.getByRole("region", { name: "Photos" }), {
      dataTransfer: fakeTransfer([broken, fileEntry(good)]),
    });

    await waitFor(() => {
      expect(onFiles).toHaveBeenCalledWith(
        [good],
        [{ name: "bad.HEIC", message: en.intake.unreadable }],
      );
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("tells the user when nothing in a drop can be read", async () => {
    const onFiles = vi.fn();
    renderWithClient(<DropZone label="Photos" onFiles={onFiles} />);
    const broken = {
      isFile: true,
      isDirectory: false,
      name: "x",
      file: (_ok: unknown, fail: (error: Error) => void) => {
        fail(new Error("gone"));
      },
    } as unknown as FileSystemFileEntry;

    fireEvent.drop(screen.getByRole("region", { name: "Photos" }), {
      dataTransfer: fakeTransfer([broken]),
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(en.intake.readFailed);
    });
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("claims a drop on a disabled zone without delivering it", () => {
    const onFiles = vi.fn();
    renderWithClient(<DropZone label="Photos" disabled onFiles={onFiles} />);
    const transfer = { ...fakeTransfer([fileEntry(fakeFile("A.HEIC"))]), dropEffect: "copy" };

    const zone = screen.getByRole("region", { name: "Photos" });
    const over = createEvent.dragOver(zone, { dataTransfer: transfer });
    fireEvent(zone, over);
    const drop = createEvent.drop(zone, { dataTransfer: transfer });
    fireEvent(zone, drop);

    expect(over.defaultPrevented).toBe(true);
    expect(transfer.dropEffect).toBe("none");
    expect(drop.defaultPrevented).toBe(true);
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("queues a drop on a busy zone behind the running upload", async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const started: string[] = [];
    fakeServer({
      "POST /api/cases/:case_id/photos": async (request): Promise<UploadResult> => {
        const name = ((request.body as FormData).get("files") as File).name;
        started.push(name);
        if (name === "A.HEIC") await held;
        return { job_id: null, added: [id(2)], skipped: [] };
      },
    });
    function Busy() {
      const { upload, progress } = useCaseUpload(CASE_ID);
      return (
        <DropZone
          label="Photos"
          queues
          busy={progress === null ? null : "Uploading"}
          onFiles={(files, unreadable) => {
            void upload(files, unreadable);
          }}
        />
      );
    }
    renderWithClient(<Busy />);
    const zone = screen.getByRole("region", { name: "Photos" });

    fireEvent.drop(zone, { dataTransfer: fakeTransfer([fileEntry(fakeFile("A.HEIC"))]) });
    await screen.findByText("Uploading");
    fireEvent.drop(zone, { dataTransfer: fakeTransfer([fileEntry(fakeFile("B.HEIC"))]) });
    await Promise.resolve();
    expect(started).toEqual(["A.HEIC"]);

    release();
    await waitFor(() => {
      expect(started).toEqual(["A.HEIC", "B.HEIC"]);
    });
  });
});
