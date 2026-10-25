export const en = {
  app: {
    name: "Dental Image Organizer",
    notFound: "This page does not exist.",
    backToLibrary: "Back to the library",
  },
  nav: {
    library: "Library",
    case: "Case",
    settings: "Settings",
    teach: "Teach",
    quit: "Quit",
  },
  shell: {
    closed: "Dental Image Organizer has closed. Click its Dock icon to reopen.",
  },
  common: {
    cancel: "Cancel",
    dismiss: "Dismiss",
  },
  errors: {
    network: "The app is not answering.",
    unexpected: "Something went wrong; see the log file.",
  },
  intake: {
    chooseFiles: "Choose files…",
    photosHelp:
      "Dragging from Photos.app does not work? In Photos, choose File → Export, then drop the exported folder.",
    readFailed: "Could not read the dropped files; try again or use Choose files.",
    unreadable: "could not be read",
    uploading: "Uploading",
  },
  skip: {
    videoMessage: "Videos are not imported.",
    sidecarMessage: "Edit sidecars are not imported.",
    rawAdvice: "DNG/ProRAW is not supported; export as HEIC or JPEG.",
    // Spec §3.1: "Skipped 12 files: 9 videos, 3 edit sidecars".
    summary: { one: "Skipped 1 file:", other: "Skipped {n} files:" },
    reasons: {
      video: { one: "1 video", other: "{n} videos" },
      sidecar: { one: "1 edit sidecar", other: "{n} edit sidecars" },
      raw: { one: "1 RAW file", other: "{n} RAW files" },
      unknown: { one: "1 other file", other: "{n} other files" },
      duplicate: { one: "1 already in the case", other: "{n} already in the case" },
      too_large: { one: "1 over 200 MB", other: "{n} over 200 MB" },
    },
  },
  export: {
    title: "Export",
    include: "What to export",
    singles: "Single photos",
    sequence: "Sequence",
    composite: "Composite",
    conflict: "If this was exported before",
    keepBoth: "Keep both",
    replace: "Replace previous export",
    // Spec §4.9: only files this app wrote and nobody changed since are deleted.
    replaceHelp: "Replacing deletes only files this app wrote and nobody changed since.",
    start: "Start export",
    starting: "Starting…",
    progress: "{done} of {total}",
    working: "Exporting…",
    files: { one: "1 file exported:", other: "{n} files exported:" },
    noFiles: "No files were exported.",
    failure: "Could not export: {message}",
    skippedPending: {
      one: "1 photo left out because it is still processing.",
      other: "{n} photos left out because they are still processing.",
    },
    skippedFailed: {
      one: "1 photo left out because it could not be processed.",
      other: "{n} photos left out because they could not be processed.",
    },
    learned: {
      one: "The classifier learned from 1 photo.",
      other: "The classifier learned from {n} photos.",
    },
    // `trained: false` still means the samples were stored; only no classifier exists yet.
    savedForClassifier: (n: number) =>
      `Saved ${String(n)} ${n === 1 ? "photo" : "photos"} for the classifier; it will suggest slots once it has enough.`,
    learningNoModel:
      "The classifier could not learn from this composite: the AI model is not downloaded.",
    learningFailed: "The classifier could not learn from this composite: {message}",
    learningUnknown: "The classifier could not learn from this composite.",
    caseFiles: { one: "{title}: 1 file", other: "{title}: {n} files" },
    caseFailed: "{title}: {message}",
    unknownCase: "A case",
    jobFailed: "The export failed: {message}",
    badResult: "The export finished, but its result could not be read.",
    reveal: "Reveal in Finder",
    download: "Download zip",
    close: "Close",
  },
  jobs: {
    lost: "The app restarted before this finished; start it again.",
  },
} as const;
