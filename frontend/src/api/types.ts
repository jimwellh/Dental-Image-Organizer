import type { components, paths } from "./schema";

// The generated file is the single source of truth; these names exist so that no component
// repeats a string union that the server owns.
type Schemas = components["schemas"];

export type CaseView = Schemas["CaseView"];
export type CaseRecord = Schemas["CaseRecord"];
export type CaseSummary = Schemas["CaseSummary"];
export type PhotoRecord = Schemas["PhotoRecord"];
export type EditRecord = Schemas["EditRecord"];
export type PatientRecord = Schemas["PatientRecord"];
export type PatientView = Schemas["PatientView"];
export type PatientsView = Schemas["PatientsView"];
export type LibraryProblemView = Schemas["LibraryProblemView"];
export type Settings = Schemas["Settings"];
export type JobView = Schemas["JobView"];
export type JobRef = Schemas["JobRef"];
export type UploadResult = Schemas["UploadResult"];
export type SkippedView = Schemas["SkippedView"];
export type ClassifierView = Schemas["ClassifierView"];
export type FlipStatusView = Schemas["FlipStatusView"];
export type ModelStatus = Schemas["ModelStatus"];
export type TeachSessionView = Schemas["TeachSessionView"];
export type TeachPhotoView = Schemas["TeachPhotoView"];
export type TeachUpload = Schemas["TeachUpload"];
export type ImportResult = Schemas["ImportResult"];
export type ExportRequest = Schemas["ExportRequest"];
// The generator marks a field with a server default as required, even in a request. `gap_px`
// defaults to 0 on the server, so a caller that does not set it leaves it out.
export type CasePatch = Omit<Schemas["CasePatch"], "sequence"> & {
  sequence?: Omit<NonNullable<Schemas["CasePatch"]["sequence"]>, "gap_px"> & { gap_px?: number };
};
export type PhotoPatch = Schemas["PhotoPatch"];
export type PatientPatch = Schemas["PatientPatch"];
export type CaseCreate = Schemas["CaseCreate"];
export type TeachConfirm = Schemas["TeachConfirm"];
export type ManualEdit = Schemas["ManualEdit"];
export type ErrorResponse = Schemas["ErrorResponse"];
export type Health = Schemas["Health"];
export type Stopping = Schemas["Stopping"];

// The optional fields below default on the server, so the generator makes them optional.
export type Slot = NonNullable<PhotoRecord["slot"]>;
export type Flip = NonNullable<EditRecord["flip"]>;
export type Stage = NonNullable<PhotoRecord["stage"]>;
export type CaseType = CaseRecord["type"];
export type AlignModel = EditRecord["model"];
export type UploadSkipReason = SkippedView["reason"];
export type JobState = JobView["state"];
export type ExportInclude = NonNullable<ExportRequest["include"]>[number];
export type ConflictMode = NonNullable<ExportRequest["conflict"]>;

export type ImageSize = NonNullable<
  NonNullable<
    paths["/api/cases/{case_id}/photos/{photo_id}/image"]["get"]["parameters"]["query"]
  >["size"]
>;
export type RenderSize = NonNullable<
  NonNullable<paths["/api/cases/{case_id}/render/sequence"]["get"]["parameters"]["query"]>["size"]
>;
export type ImportPolicy = NonNullable<
  NonNullable<paths["/api/archive/import"]["post"]["parameters"]["query"]>["on_conflict"]
>;
