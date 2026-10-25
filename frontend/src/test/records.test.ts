import { compositeView, editRecord, id, memberRecord, photoRecord, procedureView } from "./records";

// The defaults mirror what the backend's `CaseRecord` validator accepts, so a test never starts
// from a record the server would refuse.
describe("record builders", () => {
  test("a procedure case defaults to a valid output scale", () => {
    expect(procedureView([photoRecord()]).case.output_scale).toBe(1);
  });

  test("an edit carries no flip unless a test sets one", () => {
    expect(editRecord().flip).toBeNull();
    expect(editRecord({ flip: "horizontal" }).flip).toBe("horizontal");
  });

  test("a photo file matches the originals pattern", () => {
    expect(photoRecord().file).toMatch(/^originals\/[0-9a-f]{16}\.(heic|jpg|png)$/);
    expect(photoRecord({ id: id(0xabc) }).file).toBe("originals/0000000000000abc.jpg");
  });

  test("a member has the role member and no stage", () => {
    const member = memberRecord();

    expect(member.role).toBe("member");
    expect(member.stage).toBeNull();
    expect(compositeView([member]).case.photos).toEqual([member]);
  });

  test("a placed member carries a flip, and explicit overrides win", () => {
    expect(memberRecord({ slot: "upper_left" }).edit?.flip).toBe("none");
    expect(memberRecord({ slot: "upper_left", stage: "pre_op" }).stage).toBe("pre_op");
    expect(memberRecord({ edit: null }).edit).toBeNull();
  });

  test("case times are aware datetimes", () => {
    expect(procedureView([]).case.created_at).toMatch(/Z$/);
  });
});
