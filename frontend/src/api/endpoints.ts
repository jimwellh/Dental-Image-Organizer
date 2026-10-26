import { apiPath, formWithFiles, getJson, sendForm, sendJson } from "./http";
import type {
  CaseCreate,
  CasePatch,
  CaseView,
  ClassifierView,
  ExportRequest,
  Health,
  ImportPolicy,
  ImportResult,
  JobRef,
  JobView,
  ModelStatus,
  PatientPatch,
  PatientsView,
  PatientView,
  PhotoPatch,
  Settings,
  Stopping,
  TeachConfirm,
  TeachSessionView,
  TeachUpload,
  UpdateStatus,
  Updating,
  UploadResult,
} from "./types";

// One function per route the UI uses; the request and response types are the generated ones.

export const health = () => getJson<Health>(apiPath("/api/health"));
export const heartbeat = () => sendJson<undefined>("POST", apiPath("/api/heartbeat"));
export const shutdown = () => sendJson<Stopping>("POST", apiPath("/api/system/shutdown"));
export const reveal = (path: string) =>
  sendJson<undefined>("POST", apiPath("/api/system/reveal"), { path });
export const updateStatus = () => getJson<UpdateStatus>(apiPath("/api/system/update"));
export const startUpdate = () => sendJson<Updating>("POST", apiPath("/api/system/update"));

export const getSettings = () => getJson<Settings>(apiPath("/api/settings"));
export const putSettings = (settings: Settings) =>
  sendJson<Settings>("PUT", apiPath("/api/settings"), settings);

export const listPatients = () => getJson<PatientsView>(apiPath("/api/patients"));
export const patchPatient = (patientId: string, patch: PatientPatch) =>
  sendJson<PatientView>(
    "PATCH",
    apiPath("/api/patients/{patient_id}", { patient_id: patientId }),
    patch,
  );

export const createCase = (body: CaseCreate) =>
  sendJson<CaseView>("POST", apiPath("/api/cases"), body);
export const getCase = (caseId: string) =>
  getJson<CaseView>(apiPath("/api/cases/{case_id}", { case_id: caseId }));
export const patchCase = (caseId: string, patch: CasePatch) =>
  sendJson<CaseView>("PATCH", apiPath("/api/cases/{case_id}", { case_id: caseId }), patch);
export const deleteCase = (caseId: string) =>
  sendJson<undefined>("DELETE", apiPath("/api/cases/{case_id}", { case_id: caseId }));

export const uploadPhotos = (caseId: string, files: readonly File[]) =>
  sendForm<UploadResult>(
    "POST",
    apiPath("/api/cases/{case_id}/photos", { case_id: caseId }),
    formWithFiles(files),
  );
export const setReference = (caseId: string, file: File) =>
  sendForm<UploadResult>(
    "PUT",
    apiPath("/api/cases/{case_id}/reference", { case_id: caseId }),
    formWithFiles([file], "file"),
  );

export const patchPhoto = (caseId: string, photoId: string, patch: PhotoPatch) =>
  sendJson<CaseView>(
    "PATCH",
    apiPath("/api/cases/{case_id}/photos/{photo_id}", { case_id: caseId, photo_id: photoId }),
    patch,
  );
export const deletePhoto = (caseId: string, photoId: string) =>
  sendJson<CaseView>(
    "DELETE",
    apiPath("/api/cases/{case_id}/photos/{photo_id}", { case_id: caseId, photo_id: photoId }),
  );

export const autoAssign = (caseId: string) =>
  sendJson<JobRef>("POST", apiPath("/api/cases/{case_id}/composite/assign", { case_id: caseId }));

export const exportCase = (caseId: string, request: ExportRequest) =>
  sendJson<JobRef>("POST", apiPath("/api/cases/{case_id}/export", { case_id: caseId }), request);
export const exportPatient = (patientId: string, request: ExportRequest) =>
  sendJson<JobRef>(
    "POST",
    apiPath("/api/patients/{patient_id}/export", { patient_id: patientId }),
    request,
  );

// Plain URLs: the browser downloads them through `<a download>`, not through `fetch`.
export const caseExportZipUrl = (caseId: string, jobId: string) =>
  apiPath("/api/cases/{case_id}/export/{job_id}/zip", { case_id: caseId, job_id: jobId });
export const patientExportZipUrl = (patientId: string, jobId: string) =>
  apiPath("/api/patients/{patient_id}/export/{job_id}/zip", {
    patient_id: patientId,
    job_id: jobId,
  });
export const caseArchiveUrl = (caseId: string) =>
  apiPath("/api/cases/{case_id}/archive", { case_id: caseId });

export const importArchive = (file: File, onConflict: ImportPolicy) => {
  // No `last_modified`: an archive carries no capture time.
  const form = new FormData();
  form.append("file", file);
  return sendForm<ImportResult>(
    "POST",
    apiPath("/api/archive/import", {}, { on_conflict: onConflict }),
    form,
  );
};

export const getJob = (jobId: string) =>
  getJson<JobView>(apiPath("/api/jobs/{job_id}", { job_id: jobId }));
export const jobEventsUrl = (jobId: string) =>
  apiPath("/api/jobs/{job_id}/events", { job_id: jobId });

export const modelStatus = () => getJson<ModelStatus>(apiPath("/api/model/status"));
export const downloadModel = () => sendJson<JobRef>("POST", apiPath("/api/model/download"));

export const classifierStatus = () => getJson<ClassifierView>(apiPath("/api/classifier/status"));
export const retrain = () => sendJson<JobRef>("POST", apiPath("/api/classifier/retrain"));

export const startTeaching = (files: readonly File[]) =>
  sendForm<TeachUpload>("POST", apiPath("/api/classifier/teach"), formWithFiles(files));
export const getTeachSession = (sessionId: string) =>
  getJson<TeachSessionView>(
    apiPath("/api/classifier/teach/{session_id}", { session_id: sessionId }),
  );
export const confirmTeaching = (sessionId: string, body: TeachConfirm) =>
  sendJson<ClassifierView>(
    "POST",
    apiPath("/api/classifier/teach/{session_id}/confirm", { session_id: sessionId }),
    body,
  );
export const cancelTeaching = (sessionId: string) =>
  sendJson<undefined>(
    "DELETE",
    apiPath("/api/classifier/teach/{session_id}", { session_id: sessionId }),
  );
