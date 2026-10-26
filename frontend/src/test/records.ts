import type {
  CaseRecord,
  CaseSummary,
  CaseView,
  ClassifierView,
  EditRecord,
  Flip,
  JobView,
  ModelStatus,
  PatientRecord,
  PatientView,
  PatientsView,
  PhotoRecord,
  Settings,
  Slot,
  UpdateStatus,
} from "../api/types";

// Builders for the server's records. Each default is valid for the generated types; a test
// overrides only the fields it is about.

// The backend stores record times as aware datetimes, but a photo's `captured_at` is the camera's
// wall-clock time and has no zone.
const CREATED_AT = "2026-10-16T10:00:00Z";
const CAPTURED_AT = "2026-10-16T10:00:00";

// The backend's `SLOTS` order, which is also the default shooting order.
const SLOTS: Slot[] = [
  "upper_right",
  "upper_occlusal",
  "upper_left",
  "occlusion_right",
  "occlusion_front",
  "occlusion_left",
  "lower_right",
  "lower_occlusal",
  "lower_left",
];

/** A 32-digit hex id, as the server writes them. */
export function id(n: number): string {
  if (!Number.isSafeInteger(n) || n < 0) throw new RangeError(`id(n) needs a non-negative integer`);
  return n.toString(16).padStart(32, "0");
}

export function editRecord(overrides: Partial<EditRecord> = {}): EditRecord {
  return {
    algorithm_version: "align-1",
    color: { method: "identity", params: {} },
    confidence: 1,
    // A procedure case refuses a flip on any photo; composite members set it.
    flip: null,
    model: "similarity",
    output_size: [4032, 3024],
    source: "auto",
    status: "ok",
    transform: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    ...overrides,
  };
}

export function photoRecord(overrides: Partial<PhotoRecord> = {}): PhotoRecord {
  const photoId = overrides.id ?? id(1);
  return {
    id: photoId,
    role: "photo",
    state: "ready",
    stage: "intra_op",
    stage_source: "auto",
    slot: null,
    // The backend's `ORIGINAL_FILE_PATTERN`: a fixed-width hex name under `originals/`.
    file: `originals/${photoId.slice(-16)}.jpg`,
    sha256: photoId.repeat(2),
    source_name: `IMG_${photoId.slice(-4)}.HEIC`,
    width: 4032,
    height: 3024,
    captured_at: CAPTURED_AT,
    captured_at_source: "exif",
    error: null,
    edit: editRecord(),
    ...overrides,
  };
}

// A composite case holds only members, and a member has no stage; `compositeView` tests use this
// instead of `photoRecord`. A placed member carries a flip, as the backend writes one per cell.
export function memberRecord(overrides: Partial<PhotoRecord> = {}): PhotoRecord {
  const slot = overrides.slot ?? null;
  return photoRecord({
    role: "member",
    stage: null,
    edit: editRecord({ flip: slot === null ? null : "none" }),
    ...overrides,
  });
}

function patientRecord(overrides: Partial<PatientRecord> = {}): PatientRecord {
  return {
    id: id(200),
    name: "Test Patient",
    note: null,
    created_at: CREATED_AT,
    schema_version: 1,
    ...overrides,
  };
}

type CaseViewOverrides = Partial<Omit<CaseView, "case">> & { case?: Partial<CaseRecord> };

function caseView(record: CaseRecord, overrides: CaseViewOverrides): CaseView {
  const { case: caseOverrides, ...viewOverrides } = overrides;
  return {
    case: { ...record, ...caseOverrides },
    patient: patientRecord(),
    folder: "Test Patient/Case",
    flip_defaults: null,
    suggested_output_scale: null,
    ...viewOverrides,
  };
}

export function procedureView(photos: PhotoRecord[], overrides: CaseViewOverrides = {}): CaseView {
  return caseView(
    {
      id: id(100),
      patient_id: id(200),
      title: "Case",
      type: "procedure",
      created_at: CREATED_AT,
      updated_at: CREATED_AT,
      output_scale: 1,
      photos,
      exports: [],
      sequence: { photo_ids: [], gap_px: 0 },
      composite: null,
      schema_version: 1,
    },
    overrides,
  );
}

export function compositeView(photos: PhotoRecord[], overrides: CaseViewOverrides = {}): CaseView {
  return caseView(
    {
      id: id(100),
      patient_id: id(200),
      title: "Case",
      type: "composite",
      created_at: CREATED_AT,
      updated_at: CREATED_AT,
      output_scale: null,
      photos,
      exports: [],
      sequence: null,
      composite: {
        baseline_photo_id: null,
        cell_size: [1200, 800],
        gap_color: "#000000",
        gap_px: 0,
        slots_source: "auto",
      },
      schema_version: 1,
    },
    overrides,
  );
}

export function caseSummary(overrides: Partial<CaseSummary> = {}): CaseSummary {
  return {
    id: id(100),
    title: "Case",
    type: "procedure",
    folder: "Test Patient/Case",
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    photos: 0,
    pending: 0,
    failed: 0,
    needs_review: 0,
    thumbnails: [],
    ...overrides,
  };
}

export function patientView(overrides: Partial<PatientView> = {}): PatientView {
  return {
    id: id(200),
    name: "Test Patient",
    note: null,
    folder: "Test Patient",
    created_at: CREATED_AT,
    cases: [],
    ...overrides,
  };
}

export function patientsView(overrides: Partial<PatientsView> = {}): PatientsView {
  return { patients: [], problems: [], ...overrides };
}

export function settingsRecord(overrides: Partial<Settings> = {}): Settings {
  // The backend's `DEFAULT_FLIPS`: lateral views are mirrored, occlusal views flipped upside down.
  const flipStart: Record<string, Flip> = {};
  for (const slot of SLOTS) {
    flipStart[slot] = slot.endsWith("_occlusal")
      ? "vertical"
      : slot.startsWith("occlusion")
        ? "none"
        : "horizontal";
  }
  return {
    schema_version: 1,
    library_root: "/Users/dentist/DentalImageOrganizer",
    jpeg_quality: 92,
    strip_gps: true,
    cell_size: [1200, 800],
    gap_px: 0,
    gap_color: "#000000",
    shooting_order: [...SLOTS],
    flip_start: flipStart,
    flip_pins: {},
    cache_max_gb: 2,
    idle_shutdown_minutes: 30,
    update_check: true,
    ...overrides,
  };
}

export function classifierView(overrides: Partial<ClassifierView> = {}): ClassifierView {
  return {
    counts: {},
    damaged: null,
    flips: {},
    flips_damaged: null,
    min_samples_per_slot: 3,
    model_available: false,
    stale_samples: 0,
    trained: false,
    ...overrides,
  };
}

export function modelStatusView(overrides: Partial<ModelStatus> = {}): ModelStatus {
  return {
    available: false,
    download_job_id: null,
    host: "github.com",
    size_bytes: 25_000_000,
    ...overrides,
  };
}

export function jobView(overrides: Partial<JobView> = {}): JobView {
  return {
    id: id(300),
    kind: "export",
    state: "queued",
    done: 0,
    total: 0,
    message: null,
    result: null,
    error: null,
    ...overrides,
  };
}

/** No newer release, nothing failed, and no in-app update: what a development server answers. */
export function updateStatusView(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    current: "1.0.0",
    latest: null,
    available: false,
    checked_at: null,
    can_update: false,
    unavailable_reason: "In-app update works on macOS only.",
    command: "uv tool upgrade dental-image-organizer",
    last_failure: null,
    ...overrides,
  };
}
