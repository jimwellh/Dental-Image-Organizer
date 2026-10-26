import { describe, expect, it } from "vitest";
import { patientView } from "../test/records";
import { fill, foldForSearch, matchesPatient } from "./text";

describe("foldForSearch", () => {
  it("folds Unicode form, case, and spacing", () => {
    expect(foldForSearch("王小明 Café ")).toBe(foldForSearch("王小明  café"));
    // The same word typed as "e" + a combining accent, as a Mac keyboard can produce it.
    expect(foldForSearch("Cafe\u0301")).toBe(foldForSearch("Caf\u00e9"));
  });
});

describe("matchesPatient", () => {
  const patient = patientView({ name: "Bo Li", note: "Chart 12" });

  it("matches the name or the note, and everything for an empty query", () => {
    expect(matchesPatient(patient, "li")).toBe(true);
    expect(matchesPatient(patient, "chart  12")).toBe(true);
    expect(matchesPatient(patient, "   ")).toBe(true);
    expect(matchesPatient(patient, "wang")).toBe(false);
  });

  it("copes with a patient without a note", () => {
    expect(matchesPatient(patientView({ name: "Bo Li", note: null }), "chart")).toBe(false);
  });
});

describe("fill", () => {
  it("keeps $ patterns and braces of the values as they are", () => {
    expect(fill("{title}: {n} files", { title: "a $& {n} $1", n: "2" })).toBe(
      "a $& {n} $1: 2 files",
    );
  });

  it("leaves an unknown placeholder visible", () => {
    expect(fill("{x}", {})).toBe("{x}");
  });
});
