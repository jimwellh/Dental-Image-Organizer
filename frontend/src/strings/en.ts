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
  jobs: {
    lost: "The app restarted before this finished; start it again.",
  },
} as const;
