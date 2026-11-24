import { describe, expect, it } from "vitest";
import { imagesFromClipboard, pastedIntoText } from "./clipboardImages";
import { fakeClipboard } from "../test/fakeDrop";

describe("imagesFromClipboard", () => {
  it("takes the pasted image files", () => {
    const crop = new File(["png"], "image.png", { type: "image/png" });
    expect(imagesFromClipboard(fakeClipboard([crop]))).toEqual([crop]);
  });

  it("leaves out text and files that are not images", () => {
    const pdf = new File(["pdf"], "notes.pdf", { type: "application/pdf" });
    const heic = new File(["heic"], "IMG_0001.HEIC", { type: "" });
    const data = fakeClipboard([pdf, heic], { text: "IMG_0001" });
    expect(imagesFromClipboard(data)).toEqual([heic]);
  });

  it("falls back to the flat file list when the items carry no file", () => {
    const crop = new File(["png"], "image.png", { type: "image/png" });
    const data = { items: [], files: [crop] } as unknown as Pick<DataTransfer, "items" | "files">;
    expect(imagesFromClipboard(data)).toEqual([crop]);
  });

  it("finds nothing in a paste without data", () => {
    expect(imagesFromClipboard(null)).toEqual([]);
  });
});

describe("pastedIntoText", () => {
  it("tells text fields from controls and the page", () => {
    const text = document.createElement("input");
    const radio = Object.assign(document.createElement("input"), { type: "radio" });
    const note = document.createElement("textarea");
    const editor = document.createElement("div");
    editor.setAttribute("contenteditable", "true");
    const inside = editor.appendChild(document.createElement("span"));

    expect([text, note, inside].map(pastedIntoText)).toEqual([true, true, true]);
    expect([radio, document.body, document, null].map(pastedIntoText)).toEqual([
      false,
      false,
      false,
      false,
    ]);
  });
});
