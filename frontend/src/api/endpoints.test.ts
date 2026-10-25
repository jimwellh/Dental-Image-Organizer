import { fakeServer } from "../test/fakeServer";
import { id } from "../test/records";
import { importArchive, patchPhoto, uploadPhotos } from "./endpoints";

describe("endpoints", () => {
  test("uploadPhotos posts the files to the case", async () => {
    const server = fakeServer({
      "POST /api/cases/:case_id/photos": () => ({ added: [], job_id: null, skipped: [] }),
    });

    await uploadPhotos(id(1), [new File(["a"], "a.jpg", { lastModified: 1000 })]);

    expect(server.calls).toHaveLength(1);
    expect(server.calls[0]?.params).toEqual({ case_id: id(1) });
    expect(server.calls[0]?.body).toBeInstanceOf(FormData);
  });

  test("patchPhoto sends exactly the given change", async () => {
    const server = fakeServer({ "PATCH /api/cases/:case_id/photos/:photo_id": () => ({}) });

    await patchPhoto(id(1), id(2), { reset: true });

    expect(server.calls[0]?.params).toEqual({ case_id: id(1), photo_id: id(2) });
    expect(server.calls[0]?.body).toEqual({ reset: true });
  });

  test("importArchive passes the conflict policy", async () => {
    const server = fakeServer({
      "POST /api/archive/import": () => ({}),
    });

    await importArchive(new File(["zip"], "case.zip"), "copy");

    expect(server.calls[0]?.query.get("on_conflict")).toBe("copy");
    const form = server.calls[0]?.body;
    expect(form).toBeInstanceOf(FormData);
    expect([...(form as FormData).keys()]).toEqual(["file"]);
  });
});
