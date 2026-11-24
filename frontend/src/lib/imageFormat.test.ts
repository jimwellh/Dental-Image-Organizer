import { afterEach, describe, expect, it, vi } from "vitest";
import { toUploadable, UnreadableImageError } from "./imageFormat";

// jsdom decodes no image and draws no canvas, so both are stubbed.
function stubDecoding(decode: () => Promise<ImageBitmap>) {
  const createImageBitmap = vi.fn(decode);
  vi.stubGlobal("createImageBitmap", createImageBitmap);
  const drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage,
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done) => {
    done(new Blob(["png"], { type: "image/png" }));
  });
  return { createImageBitmap, drawImage };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("toUploadable", () => {
  it("passes formats the server reads, and raw files, through untouched", async () => {
    const { createImageBitmap } = stubDecoding(() => Promise.reject(new Error("unused")));
    for (const type of ["image/jpeg", "image/png", "image/heic", "", "image/x-adobe-dng"]) {
      const file = new File(["x"], "photo", { type });
      expect(await toUploadable(file)).toBe(file);
    }
    expect(createImageBitmap).not.toHaveBeenCalled();
  });

  it("re-encodes a TIFF as a PNG with the same name and time", async () => {
    const close = vi.fn();
    const bitmap = { width: 1200, height: 800, close } as unknown as ImageBitmap;
    const { drawImage } = stubDecoding(() => Promise.resolve(bitmap));
    const tiff = new File(["tiff"], "crop.tiff", { type: "image/tiff", lastModified: 1234 });

    const converted = await toUploadable(tiff);

    expect([converted.name, converted.type, converted.lastModified]).toEqual([
      "crop.png",
      "image/png",
      1234,
    ]);
    expect(drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
    expect(close).toHaveBeenCalledOnce();
  });

  it("says which file the browser could not decode", async () => {
    stubDecoding(() => Promise.reject(new DOMException("bad", "InvalidStateError")));
    const webp = new File(["webp"], "crop.webp", { type: "image/webp" });

    const failure = toUploadable(webp);

    await expect(failure).rejects.toBeInstanceOf(UnreadableImageError);
    await expect(failure).rejects.toMatchObject({ fileName: "crop.webp" });
  });
});
