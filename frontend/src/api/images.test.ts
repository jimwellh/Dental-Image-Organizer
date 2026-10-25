import { describe, expect, it } from "vitest";
import { caseSummary, compositeView, editRecord, id, photoRecord } from "../test/records";
import {
  caseThumbUrl,
  compositeUrl,
  photoImageUrl,
  sequenceUrl,
  teachImageUrl,
  versionOf,
} from "./images";

describe("photoImageUrl", () => {
  const plain = photoRecord();
  const moved = photoRecord({
    edit: editRecord({ transform: [1, 0, 5, 0, 1, 0, 0, 0, 1] }),
  });

  it("the processed URL changes when the edit changes", () => {
    const url = (photo: typeof plain) => photoImageUrl(id(100), photo, "processed", "preview");
    expect(url(moved)).not.toBe(url(plain));
    expect(url(photoRecord())).toBe(url(plain));
  });

  it("the processed URL changes when the state changes", () => {
    const url = (state: "pending" | "ready") =>
      photoImageUrl(id(100), photoRecord({ state }), "processed", "thumb");
    expect(url("pending")).not.toBe(url("ready"));
  });

  it("the original URL does not change with the edit", () => {
    const url = (photo: typeof plain) => photoImageUrl(id(100), photo, "original", "full");
    expect(url(moved)).toBe(url(plain));
    expect(url(plain)).toContain(`v=${plain.sha256.slice(0, 16)}`);
  });

  it("urls name the variant and size", () => {
    const url = photoImageUrl(id(100), plain, "original", "thumb");
    expect(url).toContain(`/api/cases/${id(100)}/photos/${plain.id}/image`);
    expect(url).toContain("variant=original");
    expect(url).toContain("size=thumb");
  });
});

describe("render URLs", () => {
  const at = (updated_at: string) => compositeView([], { case: { updated_at } });

  it("render URLs change with every case write", () => {
    const first = at("2026-10-16T10:00:00Z");
    const second = at("2026-10-16T10:00:01Z");
    expect(sequenceUrl(first, "preview")).not.toBe(sequenceUrl(second, "preview"));
    expect(compositeUrl(first, "preview")).not.toBe(compositeUrl(second, "preview"));
    expect(compositeUrl(first, "full")).toBe(compositeUrl(at("2026-10-16T10:00:00Z"), "full"));
  });

  it("name the case and the size", () => {
    const view = at("2026-10-16T10:00:00Z");
    expect(compositeUrl(view, "full")).toContain(`/api/cases/${view.case.id}/render/composite`);
    expect(compositeUrl(view, "full")).toContain("size=full");
    expect(sequenceUrl(view, "preview")).toContain("/render/sequence");
  });
});

describe("caseThumbUrl", () => {
  it("is the processed thumbnail, versioned by the case's last write", () => {
    const url = caseThumbUrl(caseSummary({ updated_at: "2026-10-16T10:00:00Z" }), id(5));
    expect(url).toContain(`/api/cases/${id(100)}/photos/${id(5)}/image`);
    expect(url).toContain("variant=processed");
    expect(url).toContain("size=thumb");
    expect(url).not.toBe(caseThumbUrl(caseSummary({ updated_at: "2026-10-17T10:00:00Z" }), id(5)));
  });
});

describe("teachImageUrl", () => {
  it("has no version", () => {
    expect(teachImageUrl(id(9), id(5), "thumb")).toBe(
      `/api/classifier/teach/${id(9)}/photos/${id(5)}/image?size=thumb`,
    );
  });
});

describe("versionOf", () => {
  it("is the 32-bit FNV-1a hash of the JSON, in 8 hex digits", () => {
    // FNV-1a("\"a\"") with the 32-bit offset basis 0x811c9dc5 and prime 0x01000193.
    expect(versionOf("a")).toBe("61a1cfea");
    expect(versionOf([1, null])).toMatch(/^[0-9a-f]{8}$/);
    expect(versionOf([1, null])).not.toBe(versionOf([1, 0]));
  });
});
