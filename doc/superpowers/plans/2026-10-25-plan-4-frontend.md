# Plan 4 — React Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the browser UI that `dio serve` hosts: the Library, Procedure case, Composite
case, Teach, Export, and Settings screens of spec §5.2, on top of the HTTP API that Plan 3
built. After this plan a dentist can do every step of spec §3 in the browser:

1. create patients and cases, drop photos and folders, and see one skip summary per drop;
2. review, compare, and manually align procedure photos (free, two-point, four-point, Reset
   perspective, Reset to auto), build the sequence, and export;
3. fix composite slots by drag-to-swap, edit cells, pick the baseline, and export;
4. teach the classifier from past sets, and change every setting;
5. see the app close (idle shutdown or Quit) and come back when it restarts.

`npm run build` writes the UI into the Python package, and the wheel carries it.

**Architecture:**

| Layer | Contents |
|---|---|
| `frontend/src/api/` | generated OpenAPI types, a thin `fetch` client, one function per endpoint, job following over SSE, image URLs, TanStack Query hooks |
| `frontend/src/lib/` | pure code: 3 × 3 transform math, a four-route router, slots, drop and upload helpers |
| `frontend/src/components/` | shared UI: buttons, dialogs, the transform surface, the 3 × 3 slot board, the shooting-order picker |
| `frontend/src/features/` | one folder per screen or flow: shell, intake, export, library, case, procedure, ai, composite, teach, settings |
| `scripts/export_openapi.py`, `tests/support/transform_fixture.py` | the Python side of the two contracts the frontend shares with the backend: the API schema and the transform math |

Server state lives in TanStack Query; UI state stays in components (spec §5.1). Every request
goes to the page's own origin under `/api`.

**Tech Stack:** React 19, TypeScript (strict), Vite 8, Tailwind CSS v4, TanStack Query v5,
dnd-kit, Vitest with jsdom and Testing Library, ESLint and Prettier, `openapi-typescript`.
Exact versions are under Before You Start.

**Spec:** [doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md](../specs/2026-10-16-dental-image-organizer-design.md).
Read §3.1–§3.5, §4.6 (model provisioning), §4.7.2 step 1 (flips), §4.8, §4.9 (export
conflicts), §4.11 (archive), §4.12, §4.13, §5, §7.4, §7.5, and §8.1 (transform math) before
starting. Read [Plan 3](2026-10-18-plan-3-storage-server.md) "Spec Amendments" too: the API
below is the one Plan 3 shipped, which those amendments describe.

## Where This Fits

Plans 1–3 are merged (`4e61611`, `71c23a2`, `56dbc3c`). This plan is 4 of 5.

- **The backend does not change**, except for two maintainer files that state the contracts:
  `scripts/export_openapi.py` (the schema the types are generated from) and
  `tests/support/transform_fixture.py` (the shared transform fixture of spec §8.1).
- **Plan 5 (distribution)** adds what spec §5 names but this plan leaves out:
  - the header's update banner and **Update now** (§5.2, §7.3);
  - the "Updating…" variant of the closed notice (§5.3); `useServerConnection` returns a state
    union that Plan 5 extends with `"updating"`;
  - the Settings entry for the daily update check (§5.2).

## Spec Amendments (confirm before executing)

Task 1 writes these into the spec.

1. **Development URL (§5.4, §7.4).** `npm run dev` serves `http://localhost:5173`. Vite binds
   `localhost`, which resolved to `::1` only on the development host, so `127.0.0.1:5173`
   refused connections (measured, see Before You Start).
2. **Server connection (§5.3).** Besides the 30 s heartbeat, the UI polls `GET /api/health`
   every 5 s. The server counts as gone when neither has succeeded for 10 s. Health polls do not
   count as activity for idle shutdown; only heartbeats do.
3. **Image freshness (§5.3).** Image URLs carry a `v` query parameter, which the server ignores.
   It changes whenever the image would change. Browsers reuse an image already loaded in the
   page for the same URL whatever its `Cache-Control` says (the HTML "list of available
   images"), so without it a re-rendered photo would keep showing its old pixels.
4. **Repository structure (§6).**
   - `frontend/dev/proxy.ts` holds the dev proxy of §5.4.
   - `frontend/src/features/` holds `shell`, `intake`, `export`, `library`, `case`,
     `procedure`, `ai`, `composite`, `teach`, and `settings`.
   - `frontend/src/api/openapi.json` and `schema.d.ts` are generated and committed.
   - `scripts/export_openapi.py` writes the schema without starting a server.
   - `tests/fixtures/transform/cases.json` is the shared transform fixture, written by
     `tests/support/transform_fixture.py`.
5. **Transform fixture (§8.1).** The fixture is generated from `core.transform` (and numpy for
   the four-point homography, cross-checked against OpenCV). A pytest test fails when it is
   stale; Vitest checks `transform.ts` against it, including the CSS `matrix3d` mapping.
6. **CI (§7.5).** The frontend job also runs `prettier --check` and asserts that the built wheel
   contains `dental_image_organizer/web/index.html`.

## Before You Start

- Run `git fetch origin && git switch -c feat/frontend origin/main`. The base must contain the
  Plan 3 merge, `56dbc3c`.
- **Host:** Windows 11. Run every command in **PowerShell** from the repository root. In the
  agent's Bash sandbox, Python failed to load Pillow's `_imagingcms` DLL ("blocked by
  Application Control"); PowerShell ran it fine.
- **Node:** 24.11 or later (`node --version`). The host has 24.11.0 and npm 11.21.1.
- **Prototypes (2026-10-25).** These ran in a scratch folder against `origin/main`. They are not
  a full implementation of this plan.

  | Fact | Consequence in this plan |
  |---|---|
  | A Vite 8 proxy whose `configure` hook sets `Origin` to the backend on `proxyReq`: the backend answered the proxied `POST /api/heartbeat` with 204; without the rewrite, 403; a request without `Origin` stayed 403 | Task 1 `apiProxy`; only an `Origin` the browser sent is rewritten |
  | SSE passes through the Vite proxy unbuffered. Each event is named by the job state (`queued`, `running`, `done`, `failed`), its `id` is the job version, and the stream ends after `done` or `failed` | Task 5 `followJob` listens to named events and closes on a finished state |
  | Vite's dev server listened on `::1` only; `127.0.0.1:5173` refused | amendment 1 |
  | `openapi-typescript` 7.13 generated 3200 lines from the schema with no warnings. `dict[Slot, …]` becomes `{[key: string]: …}`; tuples stay tuples (`cell_size: [number, number]`) | Task 2 `types.ts` names the slot keys |
  | `openapi-fetch` 0.17 widens tuples in its response types (`cell_size: number[]`), so its data is not assignable to the generated `components` types | Task 4 writes a thin client typed by `components`, with paths checked against `paths` |
  | `jsdom` 30 requires Node ≥ 24.15; 29.1.1 runs on 24.11 | `jsdom` pinned to 29.1.1 |
  | `typescript-eslint` 8.72 accepts TypeScript < 6.1 and `openapi-typescript` 7.13 accepts `^5.x`; TypeScript 7.0 satisfies neither | TypeScript pinned to 5.9.3 |
  | jsdom 29 has `PointerEvent` but no `EventSource` and no `ResizeObserver`; element sizes are 0 | Task 5 stubs a fake `EventSource` globally in the test setup; Tasks 11 and 14 stub `getBoundingClientRect` |
  | Vitest imports a JSON file outside `frontend/` (`../../../tests/fixtures/…`), and `tsc` accepts it with `resolveJsonModule` | the shared fixture stays under `tests/fixtures/` |
  | hatchling leaves the gitignored `web/` folder out of the wheel; with `artifacts = ["src/dental_image_organizer/web/**"]` the wheel carries `web/index.html` and `web/assets/*` | Task 1 adds `artifacts` |
  | The schema written by `app.openapi()` holds no path or machine-specific value (`info` is `FastAPI` / `0.1.0`) | the stale check is stable across machines |
  | The scratch toolchain below passed `tsc --noEmit`, `eslint` (flat config with `reactHooks.configs.flat.recommended`), `vitest run` in jsdom, and `vite build` | Task 1 copies it |

- **Versions** (all exact in `package.json`; `package-lock.json` is committed):

  | Package | Version | | Package | Version |
  |---|---|---|---|---|
  | react, react-dom | 19.3.1 | | vitest | 5.0.5 |
  | @types/react, @types/react-dom | 19.3.0 | | jsdom | 29.1.1 |
  | @tanstack/react-query | 5.104.5 | | @testing-library/react | 16.3.3 |
  | @dnd-kit/core | 6.3.1 | | @testing-library/dom | 10.4.2 |
  | @dnd-kit/sortable | 10.0.0 | | @testing-library/user-event | 14.6.8 |
  | @dnd-kit/utilities | 3.2.2 | | @testing-library/jest-dom | 7.0.1 |
  | typescript | 5.9.3 | | eslint | 10.13.0 |
  | vite | 8.3.5 | | @eslint/js | 10.0.1 |
  | @vitejs/plugin-react | 6.1.2 | | typescript-eslint | 8.72.0 |
  | tailwindcss, @tailwindcss/vite | 4.3.3 | | eslint-plugin-react-hooks | 7.1.1 |
  | openapi-typescript | 7.13.0 | | eslint-plugin-react-refresh | 0.5.7 |
  | @types/node | 24.19.2 | | globals | 17.13.0 |
  | prettier | 3.9.10 | | | |

  `@dnd-kit/core` 6.3.1 is the stable line (peer `react >= 16.8`); `@dnd-kit/react` is still
  0.x. If a version differs when you install, report it.

## Global Constraints

Everything in the Global Constraints of Plans 1–3 still applies:

- English text; comments explain *why*;
- no image files in git;
- LF line endings;
- each commit ends with the `Co-Authored-By:` trailer of the model that writes it;
- batch isolation: one bad file never aborts a batch (§4.13).

Additions:

- **Gates before each commit.**
  - Frontend: `npm --prefix frontend run check` (ESLint, `tsc --noEmit`, `prettier --check`,
    `vitest run`). Run `npm --prefix frontend run format` first; code blocks in this plan are
    condensed.
  - When a task touches Python, the spec, or `pyproject.toml`, also the five Python gates:
    `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`,
    `uv run pytest`.
- **Strings.** Every string a user can see lives in `frontend/src/strings/en.ts` (spec §5.1).
  Components and tests import `en`; tests assert the literal text only where the spec fixes it.
- **Network (§4.13).** The page loads nothing from another origin: no CDN, no web fonts, no
  external images. Every request goes to `/api` on the page's origin. The code never sets an
  `Origin` header; the browser sends it on every `POST`, `PUT`, `PATCH`, and `DELETE`.
- **No console output.** ESLint `no-console: "error"`. Patient names must not reach a console
  or a log.
- **State.** Function components and hooks only. Server state in TanStack Query; a mutation that
  returns a `CaseView` writes it into the cache instead of refetching.
- **Errors (§4.13).** Every error body is `{"error": {"code", "message", "details"}}`. The UI
  shows `message`, which the server writes for the user. A failed mutation reaches one global
  handler that shows a notice, unless the mutation sets `meta: { quiet: true }` and handles the
  error itself.
- **Accessibility (§5.3).** Every action is reachable by keyboard; focus rings are visible
  (`:focus-visible` outline); text contrast is at least AA (4.5:1). Drag-and-drop always has a
  keyboard path (a "Move to" select, or Move up / Move down buttons).
- **Spec values to copy exactly.**

  | Item | Value | Spec |
  |---|---|---|
  | Dev proxy | `/api` → `http://127.0.0.1:8765`, `changeOrigin: true`, `Origin` rewritten to `http://127.0.0.1:8765` | §5.4 |
  | Build output | `src/dental_image_organizer/web/`, emptied on each build | §5.4 |
  | Heartbeat | `POST /api/heartbeat` every 30 s and at once when the tab becomes visible | §5.3 |
  | Server gone | no answer for 10 s → full-screen "Dental Image Organizer has closed. Click its Dock icon to reopen."; keep polling `/api/health`; reload when it answers | §5.3 |
  | Upload concurrency | 3 | §5.3 |
  | Filtered before upload | `.mov`, `.mp4` (video), `.aae` (sidecar), case-insensitive | §5.3 |
  | Skip summary | one per drop, grouped by reason, e.g. "Skipped 12 files: 9 videos, 3 edit sidecars" | §3.1 |
  | RAW advice | "DNG/ProRAW is not supported; export as HEIC or JPEG." | §3.1 |
  | Photos.app fallback | help text: File → Export in Photos.app, then drop the exported folder | §3.1 |
  | Model consent | "Download AI model (~25 MB) from GitHub?" (size and host from `GET /model/status`) | §4.6 |
  | Output scales | 1×, 1.5×, 2×; suggestion from `suggested_output_scale` | §3.2, §4.8 |
  | Sequence | 2–10 photos; empty selection = all in capture order | §3.2 |
  | Low confidence | `edit.status == "needs_review"` (procedure and composite) | §4.5.1, §4.6 |
  | Image sizes | `thumb` (320 px), `preview` (1600 px), `full` | §4.8 |
  | Export conflicts | "Replace previous export" (`replace`) or "Keep both" (`keep_both`, default) | §4.9 |
  | Transforms | 3 × 3 row-major, photo → output, both axes divided by the image's width; CSS `matrix3d` | §4.8, §5.1 |
  | Keyboard | arrow keys nudge the transform; `[` / `]` rotate | §5.3 |

## Review Focus

These are inputs the spec implies but never spells out, and that are likely to bite a dentist.
Each one is pinned by a test in the owning task.

1. **Dropping a whole Finder folder.** It holds `.DS_Store`, Live Photo `.MOV` files, `.AAE`
   sidecars, and subfolders with more than 100 entries (Chrome's `readEntries` returns at most
   100 per call). Only the photos upload; hidden files are not even mentioned; one summary.
   → Task 7, `walks folders and reads every batch` and `drops hidden files silently`.
2. **A picture that does not change after an edit.** The browser reuses an image it already
   loaded for the same URL, so after a manual alignment the old render would stay on screen.
   → Task 5, `the processed URL changes when the edit changes`.
3. **Leaving a case and coming back, or reloading, while photos still process.** The upload's
   job id is gone, but the screen must keep refreshing until no photo is pending.
   → Task 5, `useCase keeps refreshing while a photo is pending, then stops`.
4. **Double-clicks on buttons that start work.** Many users double-click everything. Export must
   start one job.
   → Task 8, `a double click starts one export`.
5. **A trackpad pinch or a wheel spin.** Each fires dozens of wheel events, and each `PATCH`
   refits the photo's colors (≈ 0.3 s, Plan 3 timings). One gesture must send one `PATCH`.
   → Task 11, `a wheel burst commits once`.

## File Structure

```
frontend/
├── package.json  package-lock.json  index.html  tsconfig.json
├── vite.config.ts            # dev proxy, build into the Python package, Vitest
├── eslint.config.js  .prettierrc.json  .prettierignore
├── dev/proxy.ts              # apiProxy: /api → 127.0.0.1:8765 with Origin rewritten
└── src/
    ├── main.tsx  App.tsx  index.css
    ├── strings/en.ts         # every UI string
    ├── api/
    │   ├── openapi.json  schema.d.ts   # generated (Task 2)
    │   ├── types.ts          # aliases of the generated schema
    │   ├── http.ts           # ApiError, apiPath, getJson, sendJson, sendForm, describeError
    │   ├── endpoints.ts      # one function per endpoint
    │   ├── jobs.ts           # followJob, useJob, job results
    │   ├── images.ts         # image URLs with a version
    │   └── queries.ts        # query client, keys, hooks
    ├── lib/
    │   ├── transform.ts      # 3 × 3 matrices and the CSS matrix3d
    │   ├── router.ts         # four routes on the History API
    │   ├── slots.ts          # slot order, flips, moveInMap
    │   ├── text.ts           # foldForSearch
    │   ├── dropFiles.ts      # files out of a drop; client-side skips
    │   ├── skipped.ts        # the one summary per drop
    │   └── uploadQueue.ts    # uploads, three at a time
    ├── components/           # Button, Dialog, ConfirmDialog, Badge, TransformSurface,
    │                         # SlotBoard, ShootingOrderPicker
    ├── features/
    │   ├── shell/            # Shell, Header, connection, Notices, ClosedNotice
    │   ├── intake/           # DropZone, useCaseUpload
    │   ├── export/           # ExportDialog
    │   ├── library/          # LibraryScreen, CaseCard, NewCaseDialog, PatientEditDialog,
    │   │                     # ImportArchiveButton
    │   ├── case/             # CaseScreen, CaseHeader
    │   ├── procedure/        # ProcedureScreen, ReferencePanel, Timeline, PhotoTile, order,
    │   │                     # CompareView, LandmarkPicker, SequenceBuilder
    │   ├── ai/               # ModelConsent, AiStatusBanner
    │   ├── composite/        # CompositeScreen, board, CellEditor
    │   ├── teach/            # TeachScreen, progress
    │   └── settings/         # SettingsScreen, FlipTable
    └── test/                 # setup, fakeServer, fakeEventSource, records, render
scripts/export_openapi.py                 # Create: the OpenAPI document, no server
tests/support/transform_fixture.py        # Create: builds the shared fixture
tests/fixtures/transform/cases.json       # Create: generated, committed
tests/unit/test_transform_fixture.py      # Create
tests/integration/test_export_openapi.py  # Create
pyproject.toml                            # Modify: hatch artifacts
.github/workflows/ci.yml                  # Modify: frontend job
README.md                                 # Modify: status and development
```

Tests sit next to their source as `*.test.ts` / `*.test.tsx`.

---

### Task 1: Frontend workspace, the wheel, CI, and spec amendments

**Files:**

- Create: `frontend/package.json`, `frontend/package-lock.json` (by `npm install`),
  `frontend/index.html`, `frontend/tsconfig.json`, `frontend/vite.config.ts`,
  `frontend/eslint.config.js`, `frontend/.prettierrc.json`, `frontend/.prettierignore`,
  `frontend/dev/proxy.ts`, `frontend/src/main.tsx`, `frontend/src/App.tsx`,
  `frontend/src/index.css`, `frontend/src/strings/en.ts`, `frontend/src/test/setup.ts`
- Modify: `pyproject.toml`, `.github/workflows/ci.yml`,
  `doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md`
- Test: `frontend/dev/proxy.test.ts`, `frontend/src/App.test.tsx`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `apiProxy(target: string): ProxyOptions` (`frontend/dev/proxy.ts`; `ProxyOptions` from
    `vite`).
  - `en` (`frontend/src/strings/en.ts`): `export const en = { app: { name: "Dental Image
    Organizer" } } as const;`. Later tasks add one section each.
  - npm scripts: `dev`, `build`, `typecheck`, `lint`, `format`, `format:check`, `test`, `check`.
  - `App(): JSX.Element`, a placeholder heading until Task 6.

- [ ] **Step 1: Create the workspace files**

`frontend/package.json`:

```json
{
  "name": "dental-image-organizer-ui",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "engines": { "node": ">=24.11" },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "typecheck": "tsc --noEmit -p .",
    "lint": "eslint .",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "test": "vitest run",
    "check": "npm run lint && npm run typecheck && npm run format:check && npm test"
  },
  "dependencies": {
    "@dnd-kit/core": "6.3.1", "@dnd-kit/sortable": "10.0.0", "@dnd-kit/utilities": "3.2.2",
    "@tanstack/react-query": "5.104.5", "react": "19.3.1", "react-dom": "19.3.1"
  },
  "devDependencies": {
    "@eslint/js": "10.0.1", "@tailwindcss/vite": "4.3.3", "@testing-library/dom": "10.4.2",
    "@testing-library/jest-dom": "7.0.1", "@testing-library/react": "16.3.3",
    "@testing-library/user-event": "14.6.8", "@types/node": "24.19.2",
    "@types/react": "19.3.0", "@types/react-dom": "19.3.0", "@vitejs/plugin-react": "6.1.2",
    "eslint": "10.13.0", "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-react-refresh": "0.5.7", "globals": "17.13.0", "jsdom": "29.1.1",
    "openapi-typescript": "7.13.0", "prettier": "3.9.10", "tailwindcss": "4.3.3",
    "typescript": "5.9.3", "typescript-eslint": "8.72.0", "vite": "8.3.5", "vitest": "5.0.5"
  }
}
```

`frontend/tsconfig.json`: `target` ES2022; `lib` ES2023, DOM, DOM.Iterable; `module` ESNext;
`moduleResolution` bundler; `jsx` react-jsx; `strict`, `noUncheckedIndexedAccess`,
`isolatedModules`, `verbatimModuleSyntax`, `resolveJsonModule`, `skipLibCheck`, `noEmit`;
`types` `["vite/client", "vitest/globals", "@testing-library/jest-dom/vitest", "node"]`;
`include` `["src", "dev", "vite.config.ts"]`.

`frontend/vite.config.ts`:

```ts
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { apiProxy } from "./dev/proxy";

// The backend's default port (spec §4.3); `uv run dio serve --no-browser` listens there.
const BACKEND = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true, proxy: { "/api": apiProxy(BACKEND) } },
  // Outside the Vite root, so emptyOutDir must be explicit; the folder is gitignored and
  // reaches the wheel through hatch `artifacts`.
  build: { outDir: "../src/dental_image_organizer/web", emptyOutDir: true },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test/setup.ts"] },
});
```

`frontend/eslint.config.js`: `defineConfig` and `globalIgnores` from `eslint/config`;
ignore `dist` and `src/api/schema.d.ts`; for `**/*.{ts,tsx}` extend `js.configs.recommended`,
`tseslint.configs.strictTypeChecked`, `reactHooks.configs.flat.recommended`, and
`reactRefresh.configs.vite`; `languageOptions` with `globals.browser` and
`parserOptions: { projectService: true }`; rule `"no-console": "error"`.

`frontend/.prettierrc.json`: `{ "printWidth": 100 }`. `frontend/.prettierignore`:
`src/api/openapi.json`, `src/api/schema.d.ts`, `package-lock.json`.

`frontend/index.html`: `<html lang="en">`, `<meta charset="utf-8">`, the viewport meta,
`<title>Dental Image Organizer</title>`, `<div id="root">`, and `<script type="module"
src="/src/main.tsx">`. Nothing from another origin.

`frontend/src/index.css`:

```css
@import "tailwindcss";

@layer base {
  /* One visible focus ring for every control (spec §5.3). */
  :focus-visible { outline: 3px solid var(--color-sky-600); outline-offset: 2px; }
}
```

`frontend/src/test/setup.ts`: `import "@testing-library/jest-dom/vitest";` and an `afterEach`
that calls `vi.unstubAllGlobals()` and `vi.useRealTimers()`.

`frontend/src/main.tsx`: imports `./index.css` and renders `<StrictMode><App /></StrictMode>`
into `#root`.

Then run `npm --prefix frontend install` to write `package-lock.json`.

- [ ] **Step 2: Write the failing tests**

`frontend/dev/proxy.test.ts`:

```ts
import { apiProxy } from "./proxy";

const BACKEND = "http://127.0.0.1:8765";

function fakeRequest(headers: Record<string, string>) {
  return {
    headers: { ...headers } as Record<string, string | undefined>,
    getHeader(name: string) { return this.headers[name]; },
    setHeader(name: string, value: string) { this.headers[name] = value; },
  };
}

function proxyReqHook() {
  const handlers: Record<string, (request: ReturnType<typeof fakeRequest>) => void> = {};
  const proxy = { on: (event: string, fn: (r: ReturnType<typeof fakeRequest>) => void) => { handlers[event] = fn; } };
  apiProxy(BACKEND).configure?.(proxy as never, {} as never);
  return handlers.proxyReq!;
}

test("targets the backend and rewrites Host", () => {
  expect(apiProxy(BACKEND)).toMatchObject({ target: BACKEND, changeOrigin: true });
});

test("rewrites the browser's origin to the backend's own", () => {
  const request = fakeRequest({ origin: "http://localhost:5173" });
  proxyReqHook()(request);
  expect(request.headers.origin).toBe(BACKEND);
});

test("adds no origin to a request that had none", () => {
  const request = fakeRequest({});
  proxyReqHook()(request);
  expect(request.headers.origin).toBeUndefined();
});
```

`frontend/src/App.test.tsx`: `render(<App />)` → a heading named `en.app.name`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npm --prefix frontend test`
Expected: FAIL: `vite.config.ts` cannot resolve `./dev/proxy`, and `./App` does not exist.

- [ ] **Step 4: Implement `apiProxy` and the placeholder `App`**

`apiProxy` returns `{ target, changeOrigin: true, configure }`. `configure(proxy)` registers a
`proxyReq` handler that calls `setHeader("origin", target)` only when `getHeader("origin")` is
defined. Comment why: the backend's Origin check (§4.13) has no development bypass, and a
request without `Origin` must stay refused.

- [ ] **Step 5: Name the built UI in the wheel**

In `pyproject.toml`, under `[tool.hatch.build.targets.wheel]`, after `packages`:

```toml
# The built UI is gitignored, and hatchling skips ignored files unless they are named here.
artifacts = ["src/dental_image_organizer/web/**"]
```

- [ ] **Step 6: Add the frontend job to CI**

Append to `.github/workflows/ci.yml` under `jobs:`:

```yaml
  frontend:
    name: Frontend
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "24"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm run format:check
      - run: npm test
      - run: npm run build
      - name: The wheel carries the built UI
        working-directory: .
        run: |
          uv build --wheel
          unzip -l dist/*.whl | grep -q "dental_image_organizer/web/index.html"
```

- [ ] **Step 7: Apply the spec amendments**

Write amendments 1–6 (Spec Amendments above) into the spec: 1 into §5.4 and §7.4, 2 and 3
into §5.3, 4 into §6, 5 into §8.1 ("Transform math"), and 6 into §7.5.

- [ ] **Step 8: Run the tests, the build, and all gates**

Run: `npm --prefix frontend run format; npm --prefix frontend run check`
Expected: all pass.

Run: `npm --prefix frontend run build; Test-Path src/dental_image_organizer/web/index.html`
Expected: `True`.

Run: `uv build --wheel --out-dir $env:TEMP\dio-wheel-check; uv run python -c "import glob, os, zipfile; w = glob.glob(os.path.join(os.environ['TEMP'], 'dio-wheel-check', '*.whl'))[0]; print('dental_image_organizer/web/index.html' in zipfile.ZipFile(w).namelist())"`
Expected: `True`.

Run the five Python gates (Global Constraints). Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add frontend pyproject.toml .github/workflows/ci.yml doc/superpowers/specs/2026-10-16-dental-image-organizer-design.md
git commit -m "build(frontend): scaffold the React workspace, the dev proxy, and the wheel's UI"
```

---

### Task 2: The OpenAPI schema and the generated types

**Files:**

- Create: `scripts/export_openapi.py`, `frontend/src/api/types.ts`
- Create (generated): `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts`
- Modify: `frontend/package.json` (script `gen:api`), `.github/workflows/ci.yml`
- Test: `tests/integration/test_export_openapi.py`, `frontend/src/api/types.test.ts`

**Interfaces:**

- Consumes: `api.app.create_app`, `services.context.create_context`, `paths.AppDirs`,
  `paths.APP_DIR_ENV`.
- Produces:
  - `scripts/export_openapi.py`:
    - `openapi_document() -> dict[str, Any]`: inside a `TemporaryDirectory` `root`, sets
      `DIO_APP_DIR` to it for the call (restored after, so the default library root is
      `root/library`, never the home folder), builds `AppDirs(root / "config", root / "data",
      root / "cache", root / "log")`, `create_context(dirs, workers=1)`, and
      `create_app(ctx, port=8765, shutdown=lambda: None, web_dir=None)`; returns
      `app.openapi()`; `ctx.close()` in a `finally`.
    - `render(document: dict[str, Any]) -> bytes`: `json.dumps(document, indent=2,
      sort_keys=True, ensure_ascii=False) + "\n"`, UTF-8. Written as bytes, so Windows never
      turns `\n` into `\r\n`.
    - `main(argv: list[str] | None = None) -> int`: writes `render(openapi_document())` to
      `argv[0]`; returns 0.
  - npm script `gen:api`: `uv run python ../scripts/export_openapi.py src/api/openapi.json &&
    openapi-typescript src/api/openapi.json --output src/api/schema.d.ts`.
  - `frontend/src/api/types.ts`, with `type Schemas = components["schemas"]`:

    | Alias | Definition |
    |---|---|
    | `CaseView`, `CaseRecord`, `CaseSummary`, `PhotoRecord`, `EditRecord`, `PatientRecord`, `PatientView`, `PatientsView`, `LibraryProblemView`, `Settings`, `JobView`, `JobRef`, `UploadResult`, `SkippedView`, `ClassifierView`, `FlipStatusView`, `ModelStatus`, `TeachSessionView`, `TeachPhotoView`, `TeachUpload`, `ImportResult`, `ExportRequest`, `CasePatch`, `PhotoPatch`, `PatientPatch`, `CaseCreate`, `TeachConfirm`, `ManualEdit`, `ErrorResponse`, `Health`, `Stopping` | `Schemas["<same name>"]` |
    | `Slot` | `NonNullable<PhotoRecord["slot"]>` |
    | `Flip` | `NonNullable<EditRecord["flip"]>` |
    | `Stage` | `NonNullable<PhotoRecord["stage"]>` |
    | `CaseType` | `CaseRecord["type"]` |
    | `AlignModel` | `EditRecord["model"]` |
    | `UploadSkipReason` | `SkippedView["reason"]` |
    | `JobState` | `JobView["state"]` |
    | `ExportInclude` | `NonNullable<ExportRequest["include"]>[number]` |
    | `ConflictMode` | `NonNullable<ExportRequest["conflict"]>` |
    | `ImageSize` | `NonNullable<NonNullable<paths["/api/cases/{case_id}/photos/{photo_id}/image"]["get"]["parameters"]["query"]>["size"]>` (the parameters have defaults, so the generated query and its fields are optional) |
    | `RenderSize` | the same for `size` of `paths["/api/cases/{case_id}/render/sequence"]["get"]` |
    | `ImportPolicy` | the same for `on_conflict` of `paths["/api/archive/import"]["post"]` |

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_export_openapi.py` runs the script as a subprocess
(`[sys.executable, "scripts/export_openapi.py", str(out)]`, `cwd` the repository root) with
`{**os.environ, "HOME": …, "USERPROFILE": …}` pointing both at `tmp_path / "home"` and
`DIO_APP_DIR` removed (Windows needs the rest of the environment, such as `SYSTEMROOT`):

| Test | Assertions |
|---|---|
| `test_export_is_deterministic_json_with_lf` | two runs → identical bytes; no `b"\r\n"`; ends with `b"\n"`; parses; `"/api/cases/{case_id}/photos"` in `paths`; `"CaseView"` in `components.schemas` |
| `test_export_touches_no_real_folder` | after a run, `tmp_path / "home"` holds no `DentalImageOrganizer` folder |

`frontend/src/api/types.test.ts`, checked by `tsc` (Vitest runs `expectTypeOf` as a no-op):

```ts
import type { Flip, ImageSize, Slot } from "./types";

test("the generated schema names the slots, flips, and image sizes", () => {
  expectTypeOf<Slot>().toEqualTypeOf<
    | "upper_right" | "upper_occlusal" | "upper_left"
    | "occlusion_right" | "occlusion_front" | "occlusion_left"
    | "lower_right" | "lower_occlusal" | "lower_left"
  >();
  expectTypeOf<Flip>().toEqualTypeOf<"none" | "horizontal" | "vertical" | "rotate_180">();
  expectTypeOf<ImageSize>().toEqualTypeOf<"thumb" | "preview" | "full">();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/integration/test_export_openapi.py -q`
Expected: FAIL (`can't open file … export_openapi.py`).

Run: `npm --prefix frontend run typecheck`
Expected: FAIL (`Cannot find module './types'`).

- [ ] **Step 3: Implement the script, add `gen:api`, generate, and write `types.ts`**

Run: `npm --prefix frontend run gen:api` and commit its two outputs as they come.

- [ ] **Step 4: Add the stale check to CI**

In the `frontend` job, after `npm ci`:

```yaml
      - run: uv sync --locked
        working-directory: .
      - name: Generated API types are current
        run: |
          npm run gen:api
          git diff --exit-code -- src/api/openapi.json src/api/schema.d.ts
```

- [ ] **Step 5: Run the tests and all gates**

Run: `uv run pytest tests/integration/test_export_openapi.py -q` and the five Python gates,
then `npm --prefix frontend run check`.
Expected: all pass. Run `npm --prefix frontend run gen:api` again; `git status` shows no change.

- [ ] **Step 6: Commit**

```bash
git add scripts/export_openapi.py tests/integration/test_export_openapi.py frontend/package.json frontend/src/api .github/workflows/ci.yml
git commit -m "feat(ui): generate the API types from the OpenAPI schema and check they are current"
```

---

### Task 3: Shared transform math

**Files:**

- Create: `tests/support/transform_fixture.py`, `tests/fixtures/transform/cases.json`
  (generated), `frontend/src/lib/transform.ts`
- Test: `tests/unit/test_transform_fixture.py`, `frontend/src/lib/transform.test.ts`

**Interfaces:**

- Consumes: `core.transform` (`similarity`, `normalize`, `invert`, `map_points`, `to_pixel`,
  `fit_similarity`, `closest_similarity`).
- Produces:
  - `tests/support/transform_fixture.py`: `FIXTURE: Path` (`tests/fixtures/transform/cases.json`),
    `build_fixture() -> dict[str, Any]`, and `main() -> None`, which writes
    `json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"` as bytes. Run it with
    `uv run python tests/support/transform_fixture.py`.
  - `frontend/src/lib/transform.ts`. Matrices are row-major and map photo points to output
    points, both divided by the image's width (spec §4.8):

    | Export | Contract |
    |---|---|
    | `type Mat3 = readonly [number × 9]` | row-major |
    | `type Point = readonly [number, number]` | |
    | `identity(): Mat3` | |
    | `fromArray(values: readonly number[]): Mat3` | throws `RangeError` unless 9 finite numbers |
    | `multiply(a: Mat3, b: Mat3): Mat3` | `a · b` |
    | `normalize(h: Mat3): Mat3` | divided by `h[8]` |
    | `invert(h: Mat3): Mat3` | normalized, as `core.transform.invert` |
    | `mapPoint(h: Mat3, p: Point): Point` | with the perspective divide |
    | `translation(tx, ty)`, `scaling(s)` | as `core.transform` |
    | `similarity(scale, angleDeg, tx, ty, mirror = false): Mat3` | as `core.transform.similarity` |
    | `translateBy(h, dx, dy): Mat3` | `translation(dx, dy) · h`: moves the photo in output space |
    | `scaleAbout(h, factor, pivot: Point): Mat3` | scales the photo about an output point |
    | `rotateAbout(h, deg, pivot: Point): Mat3` | rotates the photo about an output point; positive is clockwise on screen (y points down) |
    | `fitSimilarity(src: readonly Point[], dst: readonly Point[]): Mat3` | as `fit_similarity`, including the mirror rule (`error < best − 1e-12`); at least 2 pairs; `RangeError` when the source points coincide |
    | `homographyFromPoints(src: readonly Point[], dst: readonly Point[]): Mat3` | exactly 4 pairs; `h[8] = 1`; `RangeError` when the system is singular (three points on a line) |
    | `closestSimilarity(h: Mat3, frameWidth: number, frameHeight: number): Mat3` | port of `closest_similarity` |
    | `toPixel(h: Mat3, inWidth: number, outWidth: number): Mat3` | as `core.transform.to_pixel` |
    | `cssMatrix3d(h: Mat3, inWidth: number, outWidth: number): string` | the CSS `matrix3d(…)` of `toPixel(h, inWidth, outWidth)`; for `transform-origin: 0 0` |
    | `isAffine(h: Mat3): boolean` | last row exactly `[0, 0, 1]` |

- [ ] **Step 1: Write the fixture builder**

```python
SIMILARITIES = [  # scale, angle_deg, tx, ty, mirror
    (1.0, 0.0, 0.0, 0.0, False),
    (1.3, 12.0, 0.05, -0.02, False),
    (0.7, -30.0, 0.2, 0.1, True),
]


def build_fixture() -> dict[str, Any]:
    rng = np.random.default_rng(8)
    sims = [tf.similarity(s, a, x, y, mirror=m) for s, a, x, y, m in SIMILARITIES]
    tilt = np.array([[1.0, 0.05, 0.0], [0.02, 1.0, 0.0], [0.08, -0.05, 1.0]])
    mats = sims + [tf.normalize(s @ tilt) for s in sims]
    points = rng.uniform(0.0, 1.0, size=(5, 2))
    noisy_src = rng.uniform(0.0, 1.0, size=(6, 2))
    known = tf.similarity(1.1, 7.0, 0.03, 0.01)
    mirrored = tf.similarity(0.9, 20.0, 0.4, 0.1, mirror=True)
    quads = [  # photo corners → output corners, as clicked in the four-point mode
        ([[0.1, 0.1], [0.9, 0.12], [0.88, 0.7], [0.12, 0.68]], [[0, 0], [1, 0], [1, 0.75], [0, 0.75]]),
        ([[0.2, 0.15], [0.7, 0.1], [0.75, 0.6], [0.15, 0.55]], [[0.1, 0.1], [0.9, 0.1], [0.9, 0.6], [0.1, 0.6]]),
    ]
    return {
        "multiply": [{"a": _m(a), "b": _m(b), "expected": _m(a @ b)} for a in mats[:3] for b in mats[3:]],
        "invert": [{"h": _m(h), "expected": _m(tf.invert(h))} for h in mats],
        "map_points": [{"h": _m(h), "points": _p(points), "expected": _p(tf.map_points(h, points))} for h in mats],
        "similarity": [
            {"scale": s, "angle_deg": a, "tx": x, "ty": y, "mirror": m, "expected": _m(h)}
            for (s, a, x, y, m), h in zip(SIMILARITIES, sims, strict=True)
        ],
        "fit_similarity": [
            _fit(points[:2], tf.map_points(known, points[:2])),  # two-point mode
            _fit(noisy_src, tf.map_points(known, noisy_src) + rng.normal(0, 0.002, (6, 2))),
            _fit(points, tf.map_points(mirrored, points)),       # a mirror fits better
        ],
        "homography_from_points": [{"src": s, "dst": d, "expected": _m(_dlt(s, d))} for s, d in quads],
        "closest_similarity": [{"h": _m(h), "frame": [1.0, 0.75], "expected": _m(tf.closest_similarity(h, 1.0, 0.75))} for h in mats[3:]],
        "to_pixel": [
            {"h": _m(h), "in_width": 4032.0, "out_width": 1600.0, "points": _p(points * 4032),
             "expected_points": _p(tf.map_points(tf.to_pixel(h, 4032.0, 1600.0), points * 4032))}
            for h in (mats[1], mats[4])
        ],
    }
```

`_m` flattens a matrix to 9 floats, `_p` turns points into lists, `_fit(src, dst)` returns
`{"src", "dst", "expected": _m(tf.fit_similarity(src, dst))}`, and `_dlt(src, dst)` solves the
8 × 8 system of the four correspondences with `np.linalg.solve` and appends `h[8] = 1`.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_transform_fixture.py`:

| Test | Assertions |
|---|---|
| `test_the_shared_fixture_is_current` | `json.loads(FIXTURE.read_bytes())` equals `build_fixture()`: the same keys and lengths, every number within `1e-9` (a recursive helper; plain `==` would fail on the last bit across operating systems) |
| `test_the_four_point_reference_matches_opencv` | each `homography_from_points` case is within `1e-5` of `cv2.getPerspectiveTransform` on the float32 points |

`frontend/src/lib/transform.test.ts` imports the fixture
(`import fixture from "../../../tests/fixtures/transform/cases.json"`) and compares with
`expectClose(actual, expected, tol)`, which checks `|a − b| ≤ tol · max(1, |b|)` per number:

| Test | Assertions |
|---|---|
| `test.each(fixture.multiply)` / `invert` / `map_points` / `similarity` / `fit_similarity` / `homography_from_points` / `closest_similarity` | the TS function matches `expected` within `1e-9` |
| `matrix3d maps input pixels as to_pixel does` (each `to_pixel` case) | parse `cssMatrix3d(h, in_width, out_width)` into 16 numbers (column-major); map each point as `x' = (m0·x + m4·y + m12) / (m3·x + m7·y + m15)`, `y' = (m1·x + m5·y + m13) / (…)`; within `1e-6` of `expected_points` |
| `translateBy moves every mapped point` | `mapPoint(translateBy(h, 0.1, -0.05), p)` = `mapPoint(h, p) + (0.1, −0.05)` |
| `scaleAbout and rotateAbout keep the pivot fixed` | for `x` with `mapPoint(h, x) = pivot`, the result still maps `x` to `pivot` |
| `fromArray refuses anything but nine finite numbers` | 8 numbers, or a `NaN` → `RangeError` |
| `degenerate landmarks are refused` | `fitSimilarity` of two equal source points, and `homographyFromPoints` with three source points on a line → `RangeError` |
| `isAffine` | true for a similarity; false for a homography of the fixture |

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_transform_fixture.py -q`
Expected: FAIL (`ModuleNotFoundError: support.transform_fixture` or a missing fixture file).

Run: `npm --prefix frontend test -- src/lib/transform.test.ts`
Expected: FAIL (`Failed to resolve import "./transform"`).

- [ ] **Step 4: Generate the fixture and implement `transform.ts`**

Run `uv run python tests/support/transform_fixture.py`.

`cssMatrix3d` turns `P = toPixel(h, inWidth, outWidth)` into
`matrix3d(P0, P3, 0, P6, P1, P4, 0, P7, 0, 0, 1, 0, P2, P5, 0, P8)` (CSS lists the 4 × 4
column by column; the 2D homography sits in rows and columns 1, 2, and 4). Print each number
with `String(n)`, never `toFixed`: a homography's perspective terms are ~1e-5 per pixel and would
round to 0. `homographyFromPoints` solves the 8 × 8 system by Gaussian elimination with partial
pivoting.

`fitSimilarity` needs no SVD: in 2D, Umeyama's best proper rotation has a closed form. With the
points centered on their means (`s_i`, `d_i`), let `A = Σ (s_x·d_x + s_y·d_y)`,
`B = Σ (s_x·d_y − s_y·d_x)`, and `V = Σ (s_x² + s_y²)`. Then `θ = atan2(B, A)`,
`scale = √(A² + B²) / V`, and `t = mean(d) − scale·R(θ)·mean(s)`. The mirror candidate fits
`(−x, y)` source points and multiplies by `diag(−1, 1, 1)` on the right, as `fit_similarity` does.

- [ ] **Step 5: Run the tests and all gates**

Run: `uv run pytest tests/unit/test_transform_fixture.py -q`, the five Python gates, and
`npm --prefix frontend run check`.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/support/transform_fixture.py tests/fixtures/transform/cases.json tests/unit/test_transform_fixture.py frontend/src/lib/transform.ts frontend/src/lib/transform.test.ts
git commit -m "feat(ui): add the transform math with a fixture shared with core.transform"
```

---

### Task 4: The HTTP client, the endpoints, and the test doubles

**Files:**

- Create: `frontend/src/api/http.ts`, `frontend/src/api/endpoints.ts`,
  `frontend/src/test/fakeServer.ts`, `frontend/src/test/records.ts`,
  `frontend/src/test/render.tsx`
- Modify: `frontend/src/strings/en.ts` (section `errors`), `frontend/src/test/setup.ts`
- Test: `frontend/src/api/http.test.ts`, `frontend/src/api/endpoints.test.ts`

**Interfaces:**

- Consumes: `paths` (`schema.d.ts`) and the aliases of `types.ts`.
- Produces:
  - `http.ts`:

    | Export | Contract |
    |---|---|
    | `class ApiError extends Error` | `status: number`, `code: string`, `details: unknown`; `message` is the server's |
    | `apiPath<P extends keyof paths>(template: P, params?: Record<string, string>, query?: Record<string, string \| undefined>): string` | fills `{name}` with `encodeURIComponent`; appends the defined query values; the template is checked against `paths` at compile time |
    | `getJson<T>(url: string): Promise<T>` | |
    | `sendJson<T>(method: "POST" \| "PUT" \| "PATCH" \| "DELETE", url: string, body?: unknown): Promise<T>` | JSON body with `Content-Type: application/json`; a 204 resolves to `undefined` |
    | `sendForm<T>(method: "POST" \| "PUT", url: string, form: FormData): Promise<T>` | the browser sets the multipart boundary and `Content-Length` (§4.13 needs it) |
    | `formWithFiles(files: readonly File[], field?: string): FormData` | each file under `field` (default `"files"`), plus one `last_modified` per file (`File.lastModified`, ms), so the server's mtime fallback (§4.4 step 5) gets the real time |
    | `describeError(error: unknown): string` | an `ApiError`'s message; for `invalid_request` with a list of `details`, adds `"<last loc>: <msg>"` of the first detail; anything else → `en.errors.unexpected` |

    A response that is not OK is parsed as `ErrorResponse`; if that fails, `ApiError(status,
    "http_<status>", en.errors.unexpected)`. A rejected `fetch` becomes `ApiError(0, "network",
    en.errors.network)`.
  - `endpoints.ts`, one function per route the UI uses (the request and response types are the
    aliases of `types.ts`):

    | Function | Route |
    |---|---|
    | `health()`, `heartbeat()`, `shutdown()`, `reveal(path)` | `GET /health`, `POST /heartbeat`, `POST /system/shutdown`, `POST /system/reveal` |
    | `getSettings()`, `putSettings(settings)` | `GET` / `PUT /settings` |
    | `listPatients()`, `patchPatient(patientId, patch)` | `GET /patients`, `PATCH /patients/{id}` |
    | `createCase(body)`, `getCase(caseId)`, `patchCase(caseId, patch)`, `deleteCase(caseId)` | `/cases`, `/cases/{id}` |
    | `uploadPhotos(caseId, files)`, `setReference(caseId, file)` | `POST /cases/{id}/photos`, `PUT /cases/{id}/reference` (field `file`, one `last_modified`) |
    | `patchPhoto(caseId, photoId, patch)`, `deletePhoto(caseId, photoId)` | `/cases/{id}/photos/{pid}` → `CaseView` |
    | `autoAssign(caseId)` | `POST /cases/{id}/composite/assign` → `JobRef` |
    | `exportCase(caseId, request)`, `exportPatient(patientId, request)` | → `JobRef` |
    | `caseExportZipUrl(caseId, jobId)`, `patientExportZipUrl(patientId, jobId)`, `caseArchiveUrl(caseId)` | URLs for `<a download>` |
    | `importArchive(file, onConflict: ImportPolicy)` | `POST /archive/import?on_conflict=…`; a `FormData` with the field `file` only (an archive has no capture time) |
    | `getJob(jobId)`, `jobEventsUrl(jobId)` | `GET /jobs/{id}`, the SSE URL |
    | `modelStatus()`, `downloadModel()` | `/model/status`, `/model/download` |
    | `classifierStatus()`, `retrain()` | `/classifier/status`, `/classifier/retrain` |
    | `startTeaching(files)`, `getTeachSession(sessionId)`, `confirmTeaching(sessionId, body)`, `cancelTeaching(sessionId)` | `/classifier/teach…` |

  - `test/fakeServer.ts`:
    - `interface FakeRequest { method: string; path: string; params: Record<string, string>;
      query: URLSearchParams; body: unknown }`. `body` is the parsed JSON, the `FormData`, or
      `undefined`.
    - `type Handler = (request: FakeRequest) => unknown`. A returned `Response` is sent as is,
      `undefined` as 204, anything else as 200 JSON. A handler that throws makes `fetch`
      reject, which is how a test takes the server down.
    - `fakeServer(routes?: Record<string, Handler>): FakeServer`, with route keys like
      `"GET /api/cases/:case_id"`. `FakeServer` has `calls: FakeRequest[]`, `on(route,
      handler)`, and `unmatched: string[]`. It stubs the global `fetch` with
      `vi.stubGlobal`.
    - `errorReply(status: number, code: string, message: string, details?: unknown): Response`.
    - `setup.ts` gains an `afterEach` that fails the test when the last fake server recorded
      an unmatched request.
  - `test/records.ts`: builders with valid defaults, each taking `Partial` overrides:
    `id(n: number): string` (32 hex digits), `editRecord`, `photoRecord` (role `photo`, state
    `ready`, stage `intra_op`, an identity `editRecord`, `width` 4032, `height` 3024,
    `captured_at` `"2026-10-16T10:00:00"`), `procedureView(photos, overrides?)`,
    `compositeView(photos, overrides?)`, `caseSummary`, `patientView`, `patientsView`,
    `settingsRecord` (the backend defaults), `classifierView`, `modelStatusView`, `jobView`.
  - `test/render.tsx`: `renderWithClient(ui: ReactElement): RenderResult & { client:
    QueryClient }`, with a fresh client (`retry: false`). Task 6 wraps it in the notice
    provider.

- [ ] **Step 1: Write the failing tests**

`http.test.ts`:

| Test | Assertions |
|---|---|
| `getJson returns the parsed body` | a fake `GET /api/health` → `{status: "ok", version: "1"}` |
| `an error body becomes an ApiError` | `errorReply(409, "job_running", "Busy.")` → rejects with `ApiError` of status 409, code `job_running`, message `Busy.` |
| `a failed fetch becomes a network error` | a throwing handler → `ApiError` code `network`, message `en.errors.network` |
| `a 204 resolves to undefined` | `POST /api/heartbeat` → `undefined` |
| `sendJson sends JSON and never an Origin header` | the request's `Content-Type` is JSON; the stubbed `fetch` received no `origin` header |
| `apiPath fills and encodes the path` | `apiPath("/api/cases/{case_id}/photos/{photo_id}", { case_id: "a b", photo_id: "x/y" }, { size: "thumb", v: undefined })` → `"/api/cases/a%20b/photos/x%2Fy?size=thumb"` |
| `formWithFiles sends one last_modified per file` | two files with `lastModified` 1000 and 2000 → `getAll("files")` has both; `getAll("last_modified")` is `["1000", "2000"]` |
| `describeError names the first refused field` | `ApiError(422, "invalid_request", "The request is not valid.", [{ loc: ["body", "jpeg_quality"], msg: "Input should be less than or equal to 100" }])` → `"The request is not valid. jpeg_quality: Input should be less than or equal to 100"` |

`endpoints.test.ts`:

| Test | Assertions |
|---|---|
| `uploadPhotos posts the files to the case` | one `POST /api/cases/:case_id/photos` with `params.case_id` and a `FormData` body |
| `patchPhoto sends exactly the given change` | body `{ reset: true }` |
| `importArchive passes the conflict policy` | `query.get("on_conflict") === "copy"` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/api`
Expected: FAIL (`Failed to resolve import "./http"`).

- [ ] **Step 3: Implement `http.ts`, `endpoints.ts`, and the test helpers; add `en.errors`**

`en.errors`: `network: "The app is not answering."`, `unexpected: "Something went wrong; see the
log file."`.

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api frontend/src/test frontend/src/strings/en.ts
git commit -m "feat(ui): add a typed HTTP client, the endpoints, and a fake server for tests"
```

---

### Task 5: Jobs, image URLs, and the query hooks

**Files:**

- Create: `frontend/src/api/jobs.ts`, `frontend/src/api/images.ts`,
  `frontend/src/api/queries.ts`, `frontend/src/test/fakeEventSource.ts`
- Modify: `frontend/src/strings/en.ts` (section `jobs`), `frontend/src/test/setup.ts`
- Test: `frontend/src/api/jobs.test.ts`, `frontend/src/api/images.test.ts`,
  `frontend/src/api/queries.test.tsx`

**Interfaces:**

- Consumes: Task 4 (`endpoints`, `ApiError`, the fake server and records).
- Produces:
  - `jobs.ts`:

    | Export | Contract |
    |---|---|
    | `interface EventSourceLike` | `readonly readyState: number`; `addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void`; `close(): void`; `onerror: ((event: Event) => void) \| null` |
    | `followJob(jobId: string, onUpdate: (job: JobView) => void): () => void` | opens `new EventSource(jobEventsUrl(jobId))` (looked up when called, so the test stub applies) and types it as `EventSourceLike`; listens to the `queued`, `running`, `done`, and `failed` events; parses each `data` as a `JobView`; closes after `done` or `failed`. On `onerror` with `readyState === 2` (the server refused the stream, e.g. a job it no longer knows after a restart) it asks `getJob` once and reports that, or, on `job_not_found`, a failed job with `error: { code: "job_lost", message: en.jobs.lost }`. With `readyState === 0` the browser is reconnecting, so it waits. The returned function stops following. |
    | `isFinished(job: JobView): boolean` | `done` or `failed` |
    | `useJob(jobId: string \| null): JobView \| null` | `followJob` in an effect; `null` until the first event |
    | `interface ExportFailure { photo_id: string \| null; message: string }` | |
    | `interface ExportResult` | `case_id: string`; `files: string[]`; `failures: ExportFailure[]`; `skipped: { pending: number; failed: number }`; `learning: { samples: number; trained: boolean } \| { error: string; message: string \| null } \| null` (services/export.py `export_case`) |
    | `interface PatientExportResult` | `patient_id: string`; `cases: ExportResult[]`; `failed_cases: { case_id: string; message: string }[]` |
    | `exportResult(job: JobView): ExportResult`, `patientExportResult(job: JobView): PatientExportResult` | `JobView.result` is `dict[str, Any]` in the schema, so these check the shape (`Array.isArray` of the lists) and throw `TypeError` when it differs |
  - `images.ts`:

    | Export | Contract |
    |---|---|
    | `versionOf(value: unknown): string` | 32-bit FNV-1a of `JSON.stringify(value)`, 8 hex digits |
    | `photoImageUrl(caseId: string, photo: PhotoRecord, variant: "original" \| "processed", size: ImageSize): string` | `v` = `photo.sha256.slice(0, 16)` for an original; `versionOf([photo.state, photo.edit ?? null])` for a processed image |
    | `caseThumbUrl(summary: CaseSummary, photoId: string): string` | processed `thumb`, `v` = `versionOf(summary.updated_at)` |
    | `sequenceUrl(view: CaseView, size: RenderSize)`, `compositeUrl(view: CaseView, size: RenderSize)` | `v` = `versionOf(view.case.updated_at)`: every write of the case changes it |
    | `teachImageUrl(sessionId: string, photoId: string, size: "thumb" \| "preview"): string` | a session's photos never change, so no `v` |
  - `queries.ts`:

    | Export | Contract |
    |---|---|
    | `PENDING_POLL_MS = 1500` | |
    | `queryKeys` | `patients: ["patients"]`, `case(caseId): ["case", caseId]`, `settings: ["settings"]`, `classifier: ["classifier"]`, `model: ["model"]`, `teach(sessionId): ["teach", sessionId]` |
    | `createQueryClient(onMutationError: (error: unknown) => void): QueryClient` | queries: `retry: false`, `refetchOnWindowFocus: true`; a `MutationCache` whose `onError` calls `onMutationError` unless `mutation.meta?.quiet` |
    | `hasPending(view: CaseView): boolean` | any photo with `state === "pending"` |
    | `usePatients()`, `useSettings()`, `useClassifier()`, `useModelStatus()` | `useQuery` on the matching endpoint |
    | `useCase(caseId: string)` | `refetchInterval` `PENDING_POLL_MS` while `hasPending`, else `false` |
    | `useTeachSession(sessionId: string \| null)` | disabled while `null` |
    | `useCaseChange<A>(caseId: string, change: (args: A) => Promise<CaseView>)` | `useMutation`; on success writes the returned view under `queryKeys.case(caseId)` and invalidates `queryKeys.patients` |
  - `test/fakeEventSource.ts`: `class FakeEventSource implements EventSourceLike` with `static
    last: FakeEventSource | null`, `url`, `closed`, `emit(state: JobState, job?:
    Partial<JobView>)` (sends `jobView({ id: <the job id of the URL>, state, ...job })`), and
    `fail(readyState: 0 | 2)`. `setup.ts` gains a `beforeEach` that stubs the global
    `EventSource` with it and resets `last`, so components that follow jobs need no test-only
    props: jsdom has no `EventSource` of its own.

- [ ] **Step 1: Write the failing tests**

`jobs.test.ts`:

| Test | Assertions |
|---|---|
| `reports each event and closes when the job is done` | `emit("running", …)`, `emit("done", …)` → `onUpdate` got both; `closed` |
| `asks for the job once when the stream is refused` | `fail(2)`; `GET /api/jobs/:job_id` → a done job → `onUpdate` got it; one call |
| `reports a lost job when the server no longer knows it` | `fail(2)`; `GET` → `errorReply(404, "job_not_found", …)` → `onUpdate` got `state "failed"`, `error.code "job_lost"` |
| `waits while the browser reconnects` | `fail(0)` → no `GET` |
| `the returned function stops following` | after it, `closed` and later events are ignored |
| `exportResult reads an export job and refuses anything else` | a result with `files`, `failures`, `skipped`, `learning` → typed; `{ photos: [] }` → `TypeError` |

`images.test.ts`:

| Test | Assertions |
|---|---|
| `the processed URL changes when the edit changes` (Review Focus 2) | two photos that differ only in `edit.transform` → different processed URLs; the same photo → the same URL |
| `the original URL does not change with the edit` | same original URL for both |
| `render URLs change with every case write` | two views that differ only in `case.updated_at` → different `sequenceUrl` |
| `urls name the variant and size` | `photoImageUrl(…, "original", "thumb")` contains `variant=original` and `size=thumb` |

`queries.test.tsx` (`renderHook` with the client of `renderWithClient`, and
`vi.useFakeTimers({ shouldAdvanceTime: true })`):

| Test | Assertions |
|---|---|
| `useCase keeps refreshing while a photo is pending, then stops` (Review Focus 3) | the first `GET /api/cases/:case_id` answers a pending photo, the next ones a ready one; after `advanceTimersByTimeAsync(1500)` → 2 calls; after another 4500 → still 2 |
| `useCaseChange stores the returned view` | after `mutateAsync`, `client.getQueryData(queryKeys.case(id))` is the reply |
| `a failed mutation reaches the handler unless it is quiet` | with `createQueryClient(spy)`: a failing mutation → `spy` once; the same with `meta: { quiet: true }` → not called |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/api`
Expected: FAIL (`Failed to resolve import "./jobs"`).

- [ ] **Step 3: Implement `jobs.ts`, `images.ts`, `queries.ts`, and the fake event source**

`en.jobs.lost`: `"The app restarted before this finished; start it again."`

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api frontend/src/test frontend/src/strings/en.ts
git commit -m "feat(ui): follow jobs over SSE, version image URLs, and add the query hooks"
```

---

### Task 6: The shell: router, header, Quit, server connection, and notices

**Files:**

- Create: `frontend/src/lib/router.ts`, `frontend/src/components/Button.tsx`,
  `frontend/src/components/Dialog.tsx`, `frontend/src/components/ConfirmDialog.tsx`,
  `frontend/src/components/Badge.tsx`, `frontend/src/features/shell/connection.ts`,
  `frontend/src/features/shell/Notices.tsx`, `frontend/src/features/shell/notify.ts`,
  `frontend/src/features/shell/Header.tsx`, `frontend/src/features/shell/ClosedNotice.tsx`,
  `frontend/src/features/shell/Shell.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/test/render.tsx`,
  `frontend/src/strings/en.ts` (sections `nav`, `shell`, `common`)
- Test: `frontend/src/lib/router.test.ts`, `frontend/src/features/shell/connection.test.ts`,
  `frontend/src/features/shell/Shell.test.tsx`, `frontend/src/components/Dialog.test.tsx`

**Interfaces:**

- Consumes: Tasks 4–5 (`heartbeat`, `health`, `shutdown`, `createQueryClient`).
- Produces:
  - `router.ts`:
    - `type Route = { name: "library" } | { name: "case"; caseId: string } | { name: "teach" }
      | { name: "settings" } | { name: "notFound" }`;
    - `parseRoute(pathname: string): Route` for `/`, `/cases/<id>`, `/teach`, `/settings`;
    - `routePath(route: Route): string`;
    - `navigate(route: Route): void`: `history.pushState`, then a `popstate` event;
    - `useRoute(): Route`: `useSyncExternalStore` on `popstate`;
    - `Link(props: { to: Route; className?: string; children: ReactNode })`: an `<a href>` whose
      plain left click navigates in place; a click with a modifier key is left to the browser.
  - Components:
    - `Button(props: ButtonHTMLAttributes & { variant?: "primary" | "secondary" | "danger" })`,
      `type="button"` by default;
    - `Dialog(props: { title: string; onClose: () => void; children: ReactNode; wide?: boolean })`:
      `role="dialog"`, `aria-modal`, labelled by its title; focuses its first focusable element
      on open and gives focus back on close; `Escape` closes. No `<dialog>` element: jsdom
      does not implement `showModal`.
    - `ConfirmDialog(props: { title: string; body: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void })`;
    - `Badge(props: { tone: "neutral" | "ok" | "warn" | "error"; children: ReactNode })`.
  - `connection.ts`:
    - constants `HEARTBEAT_MS = 30_000`, `HEALTH_MS = 5_000`, `LOST_AFTER_MS = 10_000`,
      `RECONNECT_MS = 2_000`;
    - `type ConnectionState = "connected" | "closed"` (Plan 5 adds `"updating"`);
    - `useServerConnection(options?: { reload?: () => void }): { state: ConnectionState;
      markClosed: () => void }`.

    It sends a heartbeat at once, every `HEARTBEAT_MS`, and on `visibilitychange` to
    `visible`. It polls `health()` every `HEALTH_MS`. Any successful heartbeat or health call
    updates `lastOk`; when `now − lastOk ≥ LOST_AFTER_MS` the state becomes `closed`. While
    closed it polls `health()` every `RECONNECT_MS` and calls `reload` (default
    `window.location.reload()`) on the first answer. A hung request never updates `lastOk`, so
    no timeout or abort is needed. Heartbeat and health failures never raise notices.
  - `notify.ts`: `interface Notice { id: number; kind: "info" | "error"; lines: string[] }`,
    `NoticeContext`, and `useNotify(): (notice: Omit<Notice, "id">) => void`.
    `Notices.tsx`: `NoticeProvider({ children })`, which renders the list: infos in a
    `role="status"` region that clear after `INFO_NOTICE_MS = 8000`; errors in `role="alert"`,
    kept until dismissed.
  - `Header({ onQuit }: { onQuit: () => void })`: the app name (a `Link` to the library), links
    to Library and Settings, and **Quit**.
  - `ClosedNotice()`: full screen, the spec text, `role="alert"`.
  - `Shell({ children }: { children: ReactNode })`: runs `useServerConnection`; while closed it
    renders only `ClosedNotice`; otherwise `Header` and `children`. Quit calls `shutdown()` and
    then `markClosed()`.
  - `App()`: `NoticeProvider` → a `QueryClientProvider` whose client comes from
    `createQueryClient` with an `onMutationError` that notifies `describeError(error)` →
    `Shell` → the screen of `useRoute()`. Until Tasks 9–16 add them, each route renders a
    heading with its `en.nav` name; `notFound` renders `en.app.notFound` and a link to the
    library.
  - `renderWithClient` now also wraps the UI in `NoticeProvider`.

- [ ] **Step 1: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `router.test.ts` | `parses the four routes` | `/` → library; `/cases/abc` → case `abc`; `/teach`, `/settings`; `/cases/` and `/nope` → notFound |
| | `routePath inverts parseRoute` | for each route |
| | `navigate updates useRoute` | `renderHook(useRoute)`; `navigate({ name: "settings" })` → settings |
| | `a modified click is left to the browser` | `Link` clicked with `ctrlKey` → `location.pathname` unchanged |
| `connection.test.ts` (fake timers, `renderHook`) | `sends a heartbeat at once, every 30 s, and when the tab becomes visible` | 1 `POST /api/heartbeat` at mount; 2 after 30 s; 3 after a `visibilitychange` with `document.visibilityState` stubbed to `visible` |
| | `reports the server closed after 10 s without an answer` | both routes throw; at 9 s `connected`; at 10 s `closed` |
| | `reloads the page when the server answers again` | closed, then `GET /api/health` answers → `reload` called once |
| `Shell.test.tsx` | `Quit shows the closed notice at once` | click **Quit** → one `POST /api/system/shutdown`; the text `Dental Image Organizer has closed. Click its Dock icon to reopen.` is shown and the header is gone |
| `Dialog.test.tsx` | `Escape closes the dialog and focus returns` | focus starts in the dialog; `Escape` → `onClose`; focus back on the opener |
| `App.test.tsx` (rewritten) | `the app frames the library` | a fake server for `POST /api/heartbeat`, `GET /api/health`, and `GET /api/patients` (an empty `patientsView()`, declared now so Task 9 needs no change here) → the header's link named `en.app.name` and the **Quit** button |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/lib/router.test.ts src/features/shell src/components`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement the router, the components, the shell, and `App`**

`en.shell.closed` is the spec text verbatim. `en.nav`: `library: "Library"`, `settings:
"Settings"`, `teach: "Teach"`, `quit: "Quit"`.

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the shell with routes, Quit, the server connection, and notices"
```

---

### Task 7: Intake: the drop zone, folders, uploads, and the skip summary

**Files:**

- Create: `frontend/src/lib/dropFiles.ts`, `frontend/src/lib/skipped.ts`,
  `frontend/src/lib/uploadQueue.ts`, `frontend/src/features/intake/DropZone.tsx`,
  `frontend/src/features/intake/useCaseUpload.ts`
- Modify: `frontend/src/strings/en.ts` (sections `intake`, `skip`)
- Test: `frontend/src/lib/dropFiles.test.ts`, `frontend/src/lib/skipped.test.ts`,
  `frontend/src/lib/uploadQueue.test.ts`, `frontend/src/features/intake/DropZone.test.tsx`

**Interfaces:**

- Consumes: `uploadPhotos`, `useNotify`, `queryKeys.case`, `SkippedView`, `UploadResult`.
- Produces:
  - `dropFiles.ts`:
    - `CLIENT_SKIPS: Readonly<Record<string, UploadSkipReason>>` = `{ ".mov": "video", ".mp4":
      "video", ".aae": "sidecar" }`;
    - `filesFromDrop(transfer: Pick<DataTransfer, "items" | "files">): Promise<File[]>`. It
      calls `webkitGetAsEntry()` on **every item before the first `await`**: a `DataTransfer`
      is emptied once the drop handler returns. Directories are walked recursively, calling
      `readEntries` until it returns an empty batch (Chrome returns at most 100 per call). Items
      without entries fall back to `transfer.files`.
    - `partitionFiles(files: readonly File[]): { upload: File[]; skipped: SkippedView[] }`:
      names starting with `.` (`.DS_Store`, `._IMG_0001.JPG`) are dropped without a word; the
      `CLIENT_SKIPS` extensions, matched case-insensitively, become skipped entries with
      `en.skip.videoMessage` or `en.skip.sidecarMessage`.
  - `skipped.ts`: `skipSummary(skipped: readonly SkippedView[]): string[]`. The first line is
    `Skipped <n> file(s): <parts>`, with the parts in the order video, sidecar, raw, unknown,
    duplicate, too_large, each with its count and a singular or plural noun ("1 video", "9
    videos", "3 edit sidecars", "2 RAW files", "1 other file", "2 already in the case", "1 over
    200 MB"). When any skip is `raw`, a second line is the RAW advice. No skips → `[]`.
  - `uploadQueue.ts`:
    - `interface UploadProgress { done: number; total: number }`;
    - `interface UploadSummary { added: string[]; skipped: SkippedView[]; jobIds: string[];
      failed: { name: string; message: string }[] }`;
    - `uploadAll(send: (file: File) => Promise<UploadResult>, files: readonly File[], options?:
      { concurrency?: number; onProgress?: (progress: UploadProgress) => void }):
      Promise<UploadSummary>`, at most `concurrency` (default 3) requests in flight, one file
      per request; a request that fails lands in `failed` with `describeError`, and the others
      go on.
  - `DropZone(props: { label: string; help?: string; multiple?: boolean; disabled?: boolean;
    busy?: string | null; onFiles: (files: File[]) => void })`: a labelled region that
    highlights on drag-over, calls `filesFromDrop` on drop, and has a **Choose files…** button
    over a hidden `<input type="file">`, so it works by keyboard. `help` defaults to
    `en.intake.photosHelp`.
  - `useCaseUpload(caseId: string): { upload: (files: File[]) => Promise<void>; progress:
    UploadProgress | null }`: `partitionFiles`, then `uploadAll` with `uploadPhotos(caseId,
    [file])`, then **one** notice for the drop: the `skipSummary` of the client and server
    skips together, plus one line per failed upload. It invalidates `queryKeys.case(caseId)`
    when done.

- [ ] **Step 1: Write the failing tests**

Fake entries implement the `FileSystemFileEntry` / `FileSystemDirectoryEntry` members the code
uses (`isFile`, `isDirectory`, `name`, `file(cb)`, `createReader().readEntries(cb)`).

| File | Test | Assertions |
|---|---|---|
| `dropFiles.test.ts` | `walks folders and reads every batch` (Review Focus 1) | a folder whose reader returns 100 entries, then 3, then `[]`, plus a nested folder → all 103 + nested files |
| | `drops hidden files silently` (Review Focus 1) | `.DS_Store` and `._IMG_1.JPG` are in neither `upload` nor `skipped` |
| | `filters videos and sidecars before upload` | `IMG_1.MOV` → `video`, `IMG_1.aae` → `sidecar`, `IMG_1.HEIC` uploads |
| | `falls back to the file list without entries` | items without `webkitGetAsEntry` → `transfer.files` |
| `skipped.test.ts` | `summarizes skips by reason` | 9 `video` + 3 `sidecar` → `["Skipped 12 files: 9 videos, 3 edit sidecars"]` |
| | `names RAW files explicitly` | one `raw` → `["Skipped 1 file: 1 RAW file", "DNG/ProRAW is not supported; export as HEIC or JPEG."]` |
| | `nothing skipped, nothing said` | `[]` → `[]` |
| `uploadQueue.test.ts` | `never sends more than three at once` | 7 files, `send` resolves on demand → the in-flight count peaks at 3; all 7 sent |
| | `one failed upload does not stop the rest` | the 2nd rejects → `failed` has it; `added` has the other 6 |
| `DropZone.test.tsx` | `one drop, one summary` | drop `A.HEIC`, `B.HEIC`, `C.MOV`, `.DS_Store` on a zone wired to `useCaseUpload`; the server skips `B.HEIC` as `duplicate` → 2 uploads; one notice: `"Skipped 2 files: 1 video, 1 already in the case"` |
| | `the file button works without a drag` | choosing files through the input → `onFiles` with them |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/lib src/features/intake`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; add `en.intake` and `en.skip`**

`en.intake.photosHelp`: `"Dragging from Photos.app does not work? In Photos, choose File →
Export, then drop the exported folder."`. `en.skip.rawAdvice` is the spec sentence verbatim.

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): accept dropped files and folders with one skip summary per drop"
```

---

### Task 8: The export dialog

**Files:**

- Create: `frontend/src/features/export/ExportDialog.tsx`
- Modify: `frontend/src/strings/en.ts` (section `export`)
- Test: `frontend/src/features/export/ExportDialog.test.tsx`

**Interfaces:**

- Consumes: `exportCase`, `exportPatient`, `reveal`, `caseExportZipUrl`, `patientExportZipUrl`,
  `useJob`, `exportResult`, `patientExportResult`, `Dialog`, `queryKeys`.
- Produces:
  - `type ExportTarget = { kind: "case"; caseId: string; caseType: CaseType; folder: string } |
    { kind: "patient"; patientId: string; folder: string; cases: readonly { id: string; title:
    string }[] }`;
  - `ExportDialog(props: { target: ExportTarget; onClose: () => void })`.

Behavior:

1. **Choose.** Checkboxes for what fits the target, all checked: procedure → `singles`,
   `sequence`; composite → `composite`; patient → all three (the server leaves out what a case
   type has no use for). Radios for the conflict policy: **Keep both** (`keep_both`, default) and
   **Replace previous export** (`replace`), with a line saying that replacing deletes only files
   this app wrote and nobody changed since (§4.9).
2. **Start** is disabled while nothing is checked, and from the first click on. The request is
   sent once (Review Focus 4).
3. **Progress.** `useJob(jobId)`: `done` of `total` when `total > 0`.
4. **Result** of a case export: the number of files and their base names; each failure's message;
   `skipped.pending` and `skipped.failed` as lines about photos left out; for a composite,
   whether the classifier learned (`learning.samples`) or why not (`learning.message`). Of a
   patient export: one line per case (title from `target.cases`, file count) and one per failed
   case with its message. A failed job shows its `error.message`.
5. **After.** **Reveal in Finder** posts `reveal` with `${folder}/export` for a case and
   `folder` for a patient. **Download zip** is an `<a download>` to the zip URL of the target
   and job. On `done` it invalidates the case (or `patients`).

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `a double click starts one export` (Review Focus 4) | `userEvent.dblClick` on **Start** → one `POST /api/cases/:case_id/export` |
| `sends the chosen parts and the conflict policy` | uncheck the sequence, pick **Replace previous export** → body `{ include: ["singles"], conflict: "replace" }` |
| `shows progress, then the files, failures, and photos left out` | `FakeEventSource.last.emit("running", { done: 1, total: 3 })` → `1 of 3`; `emit("done", { result })` with `files: ["export/A_x_20261016_1000.jpg"]`, one failure, `skipped: { pending: 2, failed: 0 }` → the base name, the failure message, and the pending line are shown |
| `reveals the case's export folder` | **Reveal in Finder** → `POST /api/system/reveal` body `{ path: "Alex Wang/20261016 implant/export" }` |
| `offers the patient zip` | a patient target, a done job → the link's `href` is `/api/patients/<pid>/export/<job>/zip` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/features/export`
Expected: FAIL (unresolved import).

- [ ] **Step 3: Implement `ExportDialog`; add `en.export`**

Use the spec's labels: "Replace previous export", "Keep both", "Reveal in Finder", "Download
zip".

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the export dialog with progress, results, reveal, and zip"
```

---

### Task 9: The Library screen

**Files:**

- Create: `frontend/src/lib/text.ts`, `frontend/src/features/library/LibraryScreen.tsx`,
  `frontend/src/features/library/CaseCard.tsx`,
  `frontend/src/features/library/NewCaseDialog.tsx`,
  `frontend/src/features/library/PatientEditDialog.tsx`,
  `frontend/src/features/library/ImportArchiveButton.tsx`
- Modify: `frontend/src/App.tsx` (route `library`), `frontend/src/strings/en.ts` (section
  `library`)
- Test: `frontend/src/lib/text.test.ts`, `frontend/src/features/library/LibraryScreen.test.tsx`,
  `frontend/src/features/library/NewCaseDialog.test.tsx`

**Interfaces:**

- Consumes: `usePatients`, `createCase`, `patchPatient`, `importArchive`, `caseThumbUrl`,
  `navigate`, `Link`, `ExportDialog`, `Dialog`, `ConfirmDialog`, `Badge`, `useNotify`.
- Produces:
  - `text.ts`: `foldForSearch(text: string): string` (NFC, trim, collapse whitespace,
    `toLocaleLowerCase()`), and `matchesPatient(patient: PatientView, query: string): boolean`
    (the folded query in the folded name or note; an empty query matches all).
  - `LibraryScreen()`:
    - a search box filtering patients by `matchesPatient`;
    - **New procedure case**, **New composite case**, and **Import case archive…**;
    - the `problems` of `GET /patients` in a warning list (folder and message), so a damaged
      case is visible instead of missing (Plan 3 amendment 9);
    - per patient: the name, the note, **Edit** (`PatientEditDialog`), **Export all**
      (`ExportDialog` with a patient target), and its case cards, in the order the server
      returns them.
  - `CaseCard({ summary }: { summary: CaseSummary })`: a `Link` to the case with the type, title,
    creation date, up to four `caseThumbUrl` thumbnails (`alt=""`; the title names the card), and
    badges for `needs_review`, `pending` ("Processing n"), and `failed`.
  - `NewCaseDialog(props: { type: CaseType; patients: readonly PatientView[]; onClose: () => void })`:
    - fields: the patient name (with suggestions: up to 8 patients whose name matches as typed,
      each showing its note) and the title. Its label is "Procedure / site" for a procedure case
      and "Tag" for a composite case (§3.2, §3.3);
    - picking a suggestion selects that patient. Submitting a name whose folded form equals one
      or more existing patients' names, without a pick, first asks: each such patient (with
      note) as **Use this patient**, or **New patient** with an optional note (§3.2: two
      patients may share a name);
    - submits `{ type, title, patient_id }` or `{ type, title, patient_name, patient_note? }`,
      then invalidates `patients` and navigates to the new case.
  - `PatientEditDialog(props: { patient: PatientView; onClose: () => void })`: name and note;
    an emptied note is sent as `note: null`.
  - `ImportArchiveButton()`: a file input (`accept=".zip"`). It sends `importArchive(file, "ask")`
    as a quiet mutation. On 409 `case_exists` it asks **Skip** or **Import as a copy**, naming
    `details.title`, and sends the same file again with `skip` or `copy`. Then it notifies
    `Imported <title>` (or `Skipped <title>`) and invalidates `patients`.

- [ ] **Step 1: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `text.test.ts` | `folds Unicode form, case, and spacing` | `foldForSearch("王小明 Café ")` equals `foldForSearch("王小明  café")` |
| `LibraryScreen.test.tsx` | `filters patients by name or note, whatever the Unicode form` | patients `Café Wang` and `Bo Li` (note `chart 12`); typing `café` → only Café Wang; typing `chart 12` → only Bo Li |
| | `lists folders the library could not read` | a problem `{ folder: "X/20261016 a", message: "The file is damaged…" }` → both shown |
| | `case cards show review, processing, and failed counts` | a summary with `needs_review: 2, pending: 1, failed: 1` → three badges |
| | `opening a card goes to the case` | click → `location.pathname` is `/cases/<id>` |
| | `Export all opens the export for the patient` | → a dialog whose **Start** posts `POST /api/patients/:patient_id/export` |
| | `an archive already in the library asks to skip or copy` | first `POST /api/archive/import` (`on_conflict=ask`) → `errorReply(409, "case_exists", …, { case_id, title: "implant" })`; **Import as a copy** → a second POST with `on_conflict=copy`; a notice names `implant` |
| | `clearing a note sends null` | edit, empty the note, save → `PATCH` body `{ name: "Bo Li", note: null }` |
| `NewCaseDialog.test.tsx` | `suggests existing patients with their notes` | typing `bo` → `Bo Li` with `chart 12` |
| | `a name that matches an existing patient asks which one` | type `Bo Li` without picking, submit → the choice; **Use this patient** → body `{ type: "procedure", title: "implant", patient_id }` |
| | `or creates a new patient with a note` | the same, **New patient** with note `second` → body has `patient_name: "Bo Li"` and `patient_note: "second"` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/lib/text.test.ts src/features/library`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; route `library` to `LibraryScreen`; add `en.library`**

Use the spec's labels: "New procedure case", "New composite case", "Export all".

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the library with search, case cards, new cases, and archive import"
```

---

### Task 10: The case screen and the procedure screen

**Files:**

- Create: `frontend/src/features/case/CaseScreen.tsx`, `frontend/src/features/case/CaseHeader.tsx`,
  `frontend/src/features/procedure/order.ts`,
  `frontend/src/features/procedure/ProcedureScreen.tsx`,
  `frontend/src/features/procedure/ReferencePanel.tsx`,
  `frontend/src/features/procedure/Timeline.tsx`,
  `frontend/src/features/procedure/PhotoTile.tsx`
- Modify: `frontend/src/App.tsx` (route `case`), `frontend/src/strings/en.ts` (sections `case`,
  `procedure`, `stages`)
- Test: `frontend/src/features/procedure/order.test.ts`,
  `frontend/src/features/case/CaseScreen.test.tsx`,
  `frontend/src/features/procedure/ProcedureScreen.test.tsx`

**Interfaces:**

- Consumes: `useCase`, `useCaseChange`, `patchCase`, `patchPhoto`, `deletePhoto`, `deleteCase`,
  `setReference`, `reveal`, `caseArchiveUrl`, `photoImageUrl`, `useCaseUpload`, `partitionFiles`,
  `skipSummary`, `DropZone`, `ExportDialog`, `ConfirmDialog`, `Badge`, `navigate`.
- Produces:
  - `order.ts`:
    - `captureOrder(a: PhotoRecord, b: PhotoRecord): number`: by the wall-clock part of
      `captured_at` (`captured_at.slice(0, 19)`, the way `procedure_case.capture_order` drops
      the offset), then `id`; a photo without `captured_at` sorts last;
    - `groupByStage(photos: readonly PhotoRecord[]): { stage: Stage | null; photos: PhotoRecord[] }[]`:
      groups `pre_op`, `intra_op`, `post_op`, then `null` (not staged yet). Empty groups are left
      out; each group is in capture order, and the reference comes first in its group.
  - `CaseScreen({ caseId }: { caseId: string })`: `useCase`; while loading, `en.case.loading`;
    on `case_not_found`, `en.case.missing` and a link to the library; then `ProcedureScreen` or,
    for a composite case, `CompositeScreen` (until Task 13, a heading with the title).
  - `CaseHeader(props: { view: CaseView; onExport: () => void })`:
    - the title with **Rename** (inline input, `PATCH { title }`) and the patient name and note;
    - **Export…**, **Reveal in Finder** (`reveal(view.folder)`), **Download archive** (an
      `<a download>` to `caseArchiveUrl`);
    - **Delete case**: `ConfirmDialog`; then `deleteCase`, invalidate `patients`, and navigate
      to the library. The case moves to `.trash` (§4.11), and the dialog says so.
  - `ProcedureScreen({ view }: { view: CaseView })`: `CaseHeader`, `ReferencePanel`, a
    `DropZone` for photos (`useCaseUpload`, with its progress), and `Timeline`. Task 11 adds the
    compare view and Task 12 the sequence builder.
  - `ReferencePanel({ view }: { view: CaseView })`:
    - no reference: a `DropZone` (`multiple={false}`) labelled for the edited pre-op photo. It
      sends `setReference` with the first photo of `partitionFiles` and notifies the skip
      summary if any;
    - a pending reference: "Processing…";
    - a ready reference: its processed thumbnail, a **Locked** badge, and **Replace
      reference…**. That opens a `ConfirmDialog` saying every photo is aligned again; its
      confirm button opens the file picker (the click is the user gesture the picker needs).
      The upload button stays disabled while the upload runs;
    - **Output resolution**: radios 1×, 1.5×, 2× (`PATCH { output_scale }`). When
      `suggested_output_scale` is not null and above `output_scale`, a line suggests it, with a
      button that applies it (§3.2 step 2).
  - `Timeline(props: { view: CaseView; onCompare: (photoId: string) => void })` renders
    `groupByStage` with an `en.stages` heading per group and a `PhotoTile` per photo.
  - `PhotoTile(props: { view: CaseView; photo: PhotoRecord; onCompare: (photoId: string) => void })`:
    - the image: processed `thumb` when ready with an edit; original `thumb` when ready without
      one (no reference yet, with the badge "Waiting for the reference"); a placeholder when
      pending or failed;
    - badges: confidence as a percentage, **Needs review** (`edit.status`), **Manual**
      (`edit.source`), **Processing…** (pending), the error (failed), and a warning when
      `captured_at_source === "file_mtime"` (§4.4 step 5);
    - for a non-reference ready photo: a stage `<select>` (`PATCH { stage }`), **Compare**, and
      **Delete** (`ConfirmDialog`, then `deletePhoto`).

- [ ] **Step 1: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `order.test.ts` | `groups photos by stage in capture order, the reference first` | a reference, two intra-op out of order, one post-op, one unstaged → `[pre_op [ref], intra_op [early, late], post_op [..], null [..]]` |
| | `photos without a capture time sort last` | |
| | `the offset does not change the order` | `"2026-10-16T10:00:00+08:00"` before `"2026-10-16T11:00:00"` |
| `CaseScreen.test.tsx` | `a case that is gone offers the way back` | `GET` → `errorReply(404, "case_not_found", …)` → `en.case.missing` and a library link |
| | `deleting asks first and returns to the library` | **Delete case** → no request; confirm → `DELETE /api/cases/:case_id`; pathname `/` |
| `ProcedureScreen.test.tsx` | `marks photos that need review` | a photo with `edit.status "needs_review"` → the **Needs review** badge |
| | `changing a stage patches the photo` | select **Post-op** → `PATCH …/photos/:photo_id` body `{ stage: "post_op" }` |
| | `replacing the reference asks first and uploads once` | **Replace reference…** → no request; confirm, choose a file → one `PUT /api/cases/:case_id/reference` |
| | `suggests a larger output when the photos carry more detail` | `suggested_output_scale: 1.5`, `output_scale: 1` → the suggestion; its button → `PATCH` body `{ output_scale: 1.5 }` |
| | `warns about a photo without a capture time` | `captured_at_source: "file_mtime"` → the warning |
| | `a failed photo shows why` | `state "failed"`, `error "The file is damaged."` → the message |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/features/procedure src/features/case`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; route `case` to `CaseScreen`; add the strings**

`en.stages`: `pre_op: "Pre-op"`, `intra_op: "Intra-op"`, `post_op: "Post-op"`, `unsorted: "Not
staged yet"`. Use the spec's labels "Replace reference…" and "Needs review".

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the procedure case with the reference, timeline, and stages"
```

---

### Task 11: The compare view and manual alignment

**Files:**

- Create: `frontend/src/components/TransformSurface.tsx`,
  `frontend/src/features/procedure/CompareView.tsx`,
  `frontend/src/features/procedure/LandmarkPicker.tsx`
- Modify: `frontend/src/features/procedure/ProcedureScreen.tsx` (opens `CompareView`),
  `frontend/src/strings/en.ts` (section `compare`)
- Test: `frontend/src/components/TransformSurface.test.tsx`,
  `frontend/src/features/procedure/CompareView.test.tsx`

**Interfaces:**

- Consumes: `transform.ts` (Task 3), `photoImageUrl`, `useCaseChange`, `patchPhoto`, `Dialog`.
- Produces:
  - `TransformSurface(props: TransformSurfaceProps)`, also used by Task 14:

    ```ts
    interface TransformSurfaceProps {
      imageUrl: string;          // the photo's original preview
      outAspect: number;         // output height / output width
      matrix: Mat3;              // photo → output, normalized (spec §4.8)
      opacity?: number;          // 0–1; default 1
      label: string;             // accessible name; the surface is focusable
      children?: ReactNode;      // drawn beneath the photo: the reference, or the cell
      onChange: (matrix: Mat3) => void;   // live, on every step of a gesture
      onCommit: (matrix: Mat3) => void;   // once per gesture
    }
    ```

    Constants: `WHEEL_ZOOM_RATE = 0.002`, `NUDGE = 0.002`, `NUDGE_FAST = 0.02` (with Shift),
    `ROTATE_STEP_DEG = 0.5`, `KEY_ZOOM = 1.02`, `COMMIT_DELAY_MS = 400`.

    - **Layout.** A box `aspect-ratio: 1 / outAspect`, `overflow: hidden`, `position:
      relative`, `tabIndex={0}`, `role="application"`, labelled by `label`, with `children`
      first. The photo is an `<img>` at `left: 0; top: 0`, `width` = the box width in px,
      `transform-origin: 0 0`, `transform: cssMatrix3d(matrix, W, W)`. The box width `W` comes
      from `getBoundingClientRect()`, re-read by a `ResizeObserver` when there is one.
    - **Pointer.** Drag → `translateBy(m, dx / W, dy / W)`. Shift-drag → `rotateAbout` the box
      center by the change of the pointer's angle around it. `setPointerCapture` when the
      element has it (jsdom does not). Pointer up → `onCommit` at once.
    - **Wheel and pinch.** A native `wheel` listener with `{ passive: false }` (React's
      `onWheel` is passive, so its `preventDefault` would not stop the page from scrolling):
      `scaleAbout(m, Math.exp(−deltaY · WHEEL_ZOOM_RATE), pointer / W)`. A trackpad pinch
      arrives as `wheel` with `ctrlKey`, so it takes the same path.
    - **Keys.** Arrows → `translateBy` by `NUDGE` (`NUDGE_FAST` with Shift); `[` / `]` →
      `rotateAbout` the center by `−` / `+ROTATE_STEP_DEG`; `+` or `=` / `-` → `scaleAbout` the
      center by `KEY_ZOOM` / `1 / KEY_ZOOM`.
    - **Commit.** Wheel and key changes commit `COMMIT_DELAY_MS` after the last event, so one
      gesture sends one `PATCH` (Review Focus 5).
  - `CompareView(props: { view: CaseView; photoId: string; onClose: () => void })`, a wide
    `Dialog`:
    - modes **Onion skin**, **Split**, and **Align**; a **Photo opacity** slider (0–100,
      default 50) for onion skin and align, and a **Split position** slider (default 50) for
      split, which clips the photo with `clip-path: inset(0 0 0 <x>%)`. The images are the
      reference's and the photo's processed `preview`;
    - **Align** offers **Free**, **Two points**, and **Four points**. Free is a
      `TransformSurface` over the reference's processed preview, showing the photo's original
      preview. Its `onCommit` sends `PATCH { edit: { transform, model: edit.model } }`;
    - **Reset perspective** (enabled when `edit.model === "homography"`) sends
      `closestSimilarity(matrix, 1, outAspect)` with `model: "similarity"`; **Reset to auto**
      sends `{ reset: true }`;
    - `outAspect = edit.output_size[1] / edit.output_size[0]`; the photo's aspect is `height /
      width` of its record;
    - the live matrix is component state: it starts as `fromArray(edit.transform)`, follows
      `onChange`, and is replaced whenever the photo's `edit` in the returned view changes.
  - `LandmarkPicker(props: { count: 2 | 4; referenceUrl: string; photoUrl: string;
    referenceAspect: number; photoAspect: number; onApply: (reference: Point[], photo: Point[]) => void })`:
    two panels. Clicks alternate: a landmark on the reference, then the same landmark on the
    photo. Each point is normalized by its panel's width, and a numbered marker is drawn there.
    **Apply** is enabled when both lists hold `count` points; **Clear** starts over. Two points →
    `fitSimilarity(photo, reference)` with `model: "similarity"`. Four points →
    `homographyFromPoints(photo, reference)` with `model: "homography"`. A `RangeError`
    (degenerate points) shows `en.compare.degenerate` and sends nothing. The panels are buttons
    with instructions, and each placed marker has a **Remove last point** action, so the picker
    can be operated by keyboard and screen reader too: Enter places a point at the panel center,
    and arrow keys move the last point by `NUDGE`.

- [ ] **Step 1: Write the failing tests**

Tests stub `HTMLElement.prototype.getBoundingClientRect` to `{ left: 0, top: 0, width: 500,
height: 375 }`.

| File | Test | Assertions |
|---|---|---|
| `TransformSurface.test.tsx` | `dragging moves the photo by the dragged distance` | pointer down at (100, 100), move to (150, 120), up → the last `onChange` ≈ `translateBy(m, 0.1, 0.04)`; one `onCommit` with it |
| | `shift-dragging rotates about the center` | a quarter turn around (250, 187.5) → ≈ `rotateAbout(m, 90, [0.5, 0.375])` |
| | `a wheel burst commits once` (Review Focus 5) | 20 `wheel` events → no `onCommit`; after `advanceTimersByTime(400)` → exactly one |
| | `arrow keys nudge and brackets rotate` | `ArrowRight` → `translateBy(m, 0.002, 0)`; `]` → `rotateAbout(m, 0.5, [0.5, 0.375])`; one commit 400 ms after the last key |
| | `draws the photo with the CSS matrix` | the `<img>` style transform equals `cssMatrix3d(m, 500, 500)` |
| `CompareView.test.tsx` | `two landmark pairs apply the similarity that maps them` | click reference (100, 50), photo (120, 80), reference (400, 300), photo (380, 330) → **Apply** → one `PATCH` with `model: "similarity"`; its transform maps each photo point to its reference point within `1e-9` |
| | `four landmark pairs apply a homography` | four pairs → `model: "homography"`, mapping within `1e-9` |
| | `reset perspective sends the closest similarity` | a photo with a homography edit → body `edit.model "similarity"`, transform = `closestSimilarity(h, 1, 0.75)` |
| | `reset to auto asks the server to align again` | body `{ reset: true }` |
| | `a refused edit shows the server's reason` | `PATCH` → `errorReply(422, "invalid_edit", "The transform folds the output frame over itself.")` → that text in an alert |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/components/TransformSurface.test.tsx src/features/procedure/CompareView.test.tsx`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; open `CompareView` from **Compare**; add `en.compare`**

Use the spec's labels "Compare", "Reset perspective", and "Reset to auto".

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): compare photos and align them by hand, by two points, or by four"
```

---

### Task 12: The sequence builder

**Files:**

- Create: `frontend/src/features/procedure/SequenceBuilder.tsx`
- Modify: `frontend/src/features/procedure/ProcedureScreen.tsx`,
  `frontend/src/strings/en.ts` (section `sequence`)
- Test: `frontend/src/features/procedure/SequenceBuilder.test.tsx`

**Interfaces:**

- Consumes: `captureOrder`, `useCaseChange`, `patchCase`, `sequenceUrl`, `photoImageUrl`,
  `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`.
- Produces: `SequenceBuilder({ view }: { view: CaseView })`, with `SEQUENCE_MIN = 2` and
  `SEQUENCE_MAX = 10`.
  - The usable photos are the ready photos with an edit, the reference included, in
    `captureOrder` (as `procedure_case.sequence_photos`).
  - The selection starts as the saved `sequence.photo_ids` that are still usable, or all usable
    photos when fewer than 2 remain (an empty saved list means "all in capture order", §3.2).
  - A checkbox per usable photo adds it to the end of the selection or removes it.
  - The selection is a sortable list (pointer drag with dnd-kit, `verticalListSortingStrategy`)
    with **Move up** / **Move down** buttons on each row for the keyboard.
  - **Save sequence** is enabled when the count is within 2–10 and differs from what is saved;
    it sends `PATCH { sequence: { photo_ids } }`. Outside the bounds a line says why.
  - **Use all in capture order** sends `PATCH { sequence: { photo_ids: [] } }`.
  - The preview is `<img alt={en.sequence.preview} src={sequenceUrl(view, "preview")}>` when at
    least one photo is usable.
  - `ProcedureScreen` places it below the timeline, and its **Export…** opens `ExportDialog`.

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `starts from the saved selection, or every photo in capture order` | saved `[b, a]` → rows `b, a`; saved `[]` → all usable in capture order |
| `moving a photo up and saving sends the new order` | three selected; **Move up** on the third, **Save sequence** → body `{ sequence: { photo_ids: [a, c, b] } }` |
| `refuses more than ten photos` | 11 selected → **Save sequence** disabled and the reason shown |
| `use all in capture order clears the selection` | → body `{ sequence: { photo_ids: [] } }` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/features/procedure/SequenceBuilder.test.tsx`
Expected: FAIL (unresolved import).

- [ ] **Step 3: Implement; add `en.sequence`**

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): build the procedure sequence with reordering and a preview"
```

---

### Task 13: The AI model consent and the composite screen

**Files:**

- Create: `frontend/src/lib/slots.ts`, `frontend/src/components/SlotBoard.tsx`,
  `frontend/src/features/ai/ModelConsent.tsx`, `frontend/src/features/ai/AiStatusBanner.tsx`,
  `frontend/src/features/composite/board.ts`,
  `frontend/src/features/composite/CompositeScreen.tsx`
- Modify: `frontend/src/features/case/CaseScreen.tsx`, `frontend/src/strings/en.ts` (sections
  `slots`, `flips`, `ai`, `composite`)
- Test: `frontend/src/lib/slots.test.ts`, `frontend/src/features/composite/board.test.ts`,
  `frontend/src/features/ai/ModelConsent.test.tsx`,
  `frontend/src/features/composite/CompositeScreen.test.tsx`

**Interfaces:**

- Consumes: `useCase`, `useCaseChange`, `patchPhoto`, `patchCase`, `autoAssign`,
  `downloadModel`, `useModelStatus`, `useClassifier`, `useJob`, `photoImageUrl`, `compositeUrl`,
  `useCaseUpload`, `DropZone`, `CaseHeader`, `ExportDialog`, `ConfirmDialog`, `Link`.
- Produces:
  - `slots.ts`:
    - `SLOTS`: the nine slots in row-major order, as `core/slots.py`, `as const satisfies
      readonly Slot[]`, with a compile-time check that no `Slot` is missing;
    - `FLIPS`: `["none", "horizontal", "vertical", "rotate_180"] as const satisfies readonly
      Flip[]`;
    - `type SlotMapDraft = Partial<Record<Slot, string>>`;
    - `slotOf(map: SlotMapDraft, photoId: string): Slot | null`;
    - `moveInMap(map: SlotMapDraft, photoId: string, target: Slot | "tray"): SlotMapDraft`. It
      follows the server's `move_photo`: the photo already in `target` takes the mover's old
      slot, or the tray when the mover came from the tray; `"tray"` removes the mover; the same
      slot changes nothing. The input is never mutated.
  - `SlotBoard<T>(props: SlotBoardProps<T>)`, also used by Task 15:

    ```ts
    interface SlotBoardProps<T> {
      cells: Readonly<Record<Slot, T | null>>;
      tray: readonly T[];
      idOf: (item: T) => string;
      nameOf: (item: T) => string;            // for the "Move to" select's label
      canMove: (item: T) => boolean;
      renderItem: (item: T, where: Slot | "tray") => ReactNode;
      highlight?: (item: T) => boolean;       // low confidence
      onMove: (id: string, target: Slot | "tray") => void;
      onOpen?: (item: T) => void;             // Enter or double-click on a placed item
    }
    ```

    A 3 × 3 grid in `SLOTS` order, each cell labelled `en.slots[slot]`, and a tray below it.
    dnd-kit `DndContext` with a `PointerSensor` (`activationConstraint: { distance: 5 }`) and a
    `KeyboardSensor`; draggable ids are item ids, droppable ids the slot names and `"tray"`.
    A drop where the target differs calls `onMove`. Every movable item also has a **Move to**
    `<select>` (labelled `Move <name> to`), the tested keyboard path. Highlighted items get an
    amber ring and the text **Check placement**.
  - `board.ts`:
    - `cellsOf(view: CaseView): Record<Slot, PhotoRecord | null>`;
    - `trayOf(view: CaseView): PhotoRecord[]`: members without a slot in `captureOrder`, pending
      and failed included;
    - `moveRequest(view: CaseView, photoId: string, target: Slot | "tray"): PhotoPatch | null`:
      `null` when the photo is not ready or already there; otherwise `{ slot: target }`.
  - `ModelConsent()`: `useModelStatus`. It shows
    `en.ai.consent(mb, host)` → "Download AI model (~25 MB) from GitHub?", with `mb =
    Math.round(size_bytes / 1e6)` and `host` `"GitHub"` for `github.com`, else the host as is.
    Buttons **Download** and **Not now**. Download posts `downloadModel()` and follows the job
    (also a `download_job_id` already running) with a percentage of `done / total`. On `done` it
    invalidates `model` and `classifier`; on `failed` it shows the message and **Try again**.
    **Not now** shows `en.ai.declined`: "Photos are placed in capture order until the AI model is
    downloaded."
  - `AiStatusBanner()`: the model is unavailable → `ModelConsent`; the classifier is not trained
    → `en.ai.untrained` and a **Teach from past composites** `Link` to the teach route; trained
    → nothing.
  - `CompositeScreen({ view }: { view: CaseView })`:
    - `CaseHeader` (its export opens a composite `ExportDialog`), `AiStatusBanner`, and a
      `DropZone` (`useCaseUpload`);
    - `SlotBoard` over `cellsOf` and `trayOf`. Placed photos show their processed `thumb`, tray
      photos their original `thumb`, pending and failed photos a placeholder with their state.
      `highlight` is `edit.status === "needs_review"`, `canMove` is `state === "ready"`, and
      `onMove` sends `moveRequest` when it is not `null`. Each placed cell shows its flip
      (`en.flips`);
    - a **Baseline** `<select>`: "Frontal occlusion (default)" sends `{ composite: {
      baseline_photo_id: null } }`; each placed photo, named by its slot, sends its id;
    - **Re-run automatic assignment**: when `composite.slots_source === "manual"`, a
      `ConfirmDialog` first, since it replaces the dentist's placement. Then `autoAssign`,
      following the job and invalidating the case on `done`;
    - the live preview `<img alt={en.composite.preview} src={compositeUrl(view, "preview")}>`
      when a photo is placed.
  - `CaseScreen` renders `CompositeScreen` for composite cases.

- [ ] **Step 1: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `slots.test.ts` | `moving into a full slot swaps` | `{ upper_right: a, upper_left: b }`, move `a` → `upper_left` → `{ upper_left: a, upper_right: b }` |
| | `moving from the tray sends the displaced photo to the tray` | move `c` (tray) → `upper_left` → `{ upper_right: a, upper_left: c }` |
| | `moving to the tray empties the slot` | |
| | `the input map is not changed` | |
| | `SLOTS is row-major` | equals the nine names in the order of `core/slots.py` |
| `board.test.ts` | `nothing to send for the same slot or an unready photo` | both → `null` |
| | `the tray is in capture order` | |
| `ModelConsent.test.tsx` | `asks before downloading and shows progress` | the consent text verbatim; no request until **Download**; then `POST /api/model/download`; `emit("running", { done: 5e6, total: 25e6 })` → `20%`; `emit("done")` → `GET /api/model/status` again |
| | `declining keeps the capture-order note` | **Not now** → `en.ai.declined`; no POST |
| | `follows a download that is already running` | `download_job_id: "j"` → no POST; progress shown |
| `CompositeScreen.test.tsx` | `moving a photo to a slot patches it` | **Move IMG_0003.jpg to** → **Upper left** → `PATCH` body `{ slot: "upper_left" }` |
| | `highlights low-confidence cells` | a cell with `edit.status "needs_review"` → **Check placement** |
| | `the default baseline is sent as null` | → body `{ composite: { baseline_photo_id: null } }` |
| | `re-running the assignment asks first after manual changes` | `slots_source "manual"` → confirm before `POST …/composite/assign`; `"auto"` → posts at once |
| | `an untrained classifier offers teaching` | model available, `trained: false` → a link to `/teach` |
| | `shows the composite preview once a photo is placed` | an `img` named `en.composite.preview` whose `src` contains `/render/composite` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/lib/slots.test.ts src/features/composite src/features/ai`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; add the strings**

`en.slots`: `upper_right: "Upper right"`, `upper_occlusal: "Upper occlusal"`, `upper_left:
"Upper left"`, `occlusion_right: "Right occlusion"`, `occlusion_front: "Frontal occlusion"`,
`occlusion_left: "Left occlusion"`, `lower_right: "Lower right"`, `lower_occlusal: "Lower
occlusal"`, `lower_left: "Lower left"`. `en.flips`: `none: "No flip"`, `horizontal: "Mirror
left–right"`, `vertical: "Mirror top–bottom"`, `rotate_180: "Rotate 180°"`. `en.ai.consent` is a
function of `mb` and `host` that renders the spec sentence.

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the composite board with drag-to-swap, baseline, and the model consent"
```

---

### Task 14: The cell editor

**Files:**

- Create: `frontend/src/features/composite/CellEditor.tsx`
- Modify: `frontend/src/features/composite/CompositeScreen.tsx` (`onOpen`),
  `frontend/src/strings/en.ts` (section `cell`)
- Test: `frontend/src/features/composite/CellEditor.test.tsx`

**Interfaces:**

- Consumes: `TransformSurface`, `fromArray`, `useCaseChange`, `patchPhoto`, `photoImageUrl`,
  `FLIPS`, `Dialog`.
- Produces: `CellEditor(props: { view: CaseView; photoId: string; onClose: () => void })`, a
  `Dialog` titled by the cell's slot:
  - a `TransformSurface` with the photo's original `preview`, `outAspect = cell_size[1] /
    cell_size[0]`, `matrix = fromArray(edit.transform)`, and the case's `gap_color` as the
    background beneath. `onCommit` sends `PATCH { edit: { transform, model: "similarity" } }`.
    A composite cell takes a similarity only (§4.7.2 step 4), and the gestures keep the last
    row `[0, 0, 1]` exactly;
  - a **Flip** radio group over `FLIPS` (`en.flips`), checked at `edit.flip ??
    view.flip_defaults?.[slot]`, which sends `PATCH { flip }`. A line says that a new flip
    recomputes the cell's geometry, which drops manual pan and zoom (`composite_case.set_flip`);
  - **Reset** sends `PATCH { reset: true }`;
  - `CompositeScreen` opens it from a placed cell's `onOpen`.

- [ ] **Step 1: Write the failing tests**

`getBoundingClientRect` stubbed as in Task 11.

| Test | Assertions |
|---|---|
| `a pan commits a similarity` | drag by (50, 0) → one `PATCH` with `edit.model "similarity"` and a transform `≈ translateBy(m, 0.1, 0)` whose last row is exactly `[0, 0, 1]` |
| `choosing a flip patches the photo` | **Mirror top–bottom** → body `{ flip: "vertical" }` |
| `reset returns the cell to auto` | → body `{ reset: true }` |
| `the cell opens with Enter` | focus a placed cell in `CompositeScreen`, press Enter → a dialog titled with the slot name |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/features/composite/CellEditor.test.tsx`
Expected: FAIL (unresolved import).

- [ ] **Step 3: Implement; add `en.cell`**

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): edit composite cells with pan, zoom, rotate, flip, and reset"
```

---

### Task 15: The Teach screen

**Files:**

- Create: `frontend/src/components/ShootingOrderPicker.tsx`,
  `frontend/src/features/teach/progress.ts`, `frontend/src/features/teach/TeachScreen.tsx`
- Modify: `frontend/src/App.tsx` (route `teach`), `frontend/src/strings/en.ts` (section `teach`)
- Test: `frontend/src/components/ShootingOrderPicker.test.tsx`,
  `frontend/src/features/teach/progress.test.ts`,
  `frontend/src/features/teach/TeachScreen.test.tsx`

**Interfaces:**

- Consumes: `startTeaching`, `getTeachSession`, `confirmTeaching`, `cancelTeaching`,
  `useSettings`, `putSettings`, `useClassifier`, `useModelStatus`, `useJob`, `teachImageUrl`,
  `partitionFiles`, `skipSummary`, `DropZone`, `SlotBoard`, `moveInMap`, `SLOTS`, `FLIPS`,
  `ModelConsent`.
- Produces:
  - `ShootingOrderPicker(props: { value: readonly Slot[]; onChange: (order: Slot[]) => void;
    disabled?: boolean })`, also used by Task 16. It shows the 3 × 3 grid with each slot's
    position in `value`. **Change order** enters picking mode, with the instruction to click the
    views in the order they are shot. Each click appends a slot (a repeat is ignored); the ninth
    calls `onChange` and leaves picking mode. **Cancel** leaves without a change. Cells are
    buttons, so it works by keyboard.
  - `progress.ts`: `teachProgress(status: ClassifierView): { set: number; of: number; trained:
    boolean }` with `of = min_samples_per_slot`, `set = min(of, (smallest count over SLOTS, 0 when
    missing) + 1)`, and `trained = status.trained`.
  - `TeachScreen()`:
    1. The model must be available (§3.5 step 1); otherwise only `ModelConsent`.
    2. The shooting order: `ShootingOrderPicker` over `settings.shooting_order`. A change sends
       `putSettings({ ...settings, shooting_order })` (the whole settings, as `PUT` replaces
       them) and stores the returned settings under `queryKeys.settings`.
    3. The progress: `Set <set> of <of>`, or `en.teach.trained` once trained; then the count per
       slot (`counts[slot]` of `min_samples_per_slot`).
    4. A `DropZone` for one past set. The files go through `partitionFiles`, then one
       `startTeaching(files)` request: a session holds one set, so the files must not be split.
       Skips become one notice. A `null` `session_id` means nothing was usable; say so.
    5. While the job runs, its progress. On `done`, `getTeachSession`.
    6. The confirmation: a `SlotBoard` over a `SlotMapDraft` that starts as the session's
       `proposal`; the tray holds the ready photos not in it. `onMove` applies `moveInMap`
       locally. Each filled slot has a flip `<select>`, starting at
       `classifier.flips[slot].effective`. Failed photos are listed with their errors.
       **Confirm** sends `{ slots: draft, flips: <the filled slots only> }`, stores the returned
       `ClassifierView` under `queryKeys.classifier`, and returns to step 4 for the next set.
       **Cancel** sends `cancelTeaching` and returns to step 4.
    7. Leaving the screen with an open session cancels it (a quiet request; the server also
       empties sessions at startup, §3.5 step 5).

- [ ] **Step 1: Write the failing tests**

| File | Test | Assertions |
|---|---|---|
| `ShootingOrderPicker.test.tsx` | `clicking all nine views saves the order` | **Change order**, click the nine cells in reverse row-major order → `onChange` with that order |
| | `cancel keeps the order` | three clicks, **Cancel** → `onChange` not called |
| `progress.test.ts` | `set n of 3 follows the smallest slot count` | all counts 0 → set 1; all 1 except one 0 → set 1; all ≥ 2 → set 3; all ≥ 5 → set 3 |
| `TeachScreen.test.tsx` | `asks for the model first` | `available: false` → the consent text; no drop zone |
| | `one drop is one session` | drop 9 photos → one `POST /api/classifier/teach` holding all 9 |
| | `confirming sends the board and the flips` | session ready with a proposal; move one photo with **Move to**; set one flip → body `{ slots: <draft>, flips: <one per filled slot> }`; then `Set 2 of 3` from the returned status |
| | `cancel deletes the session` | → `DELETE /api/classifier/teach/:session_id` |
| | `a change of shooting order saves the whole settings` | → `PUT /api/settings` whose body equals the settings with the new `shooting_order` |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/components/ShootingOrderPicker.test.tsx src/features/teach`
Expected: FAIL (unresolved imports).

- [ ] **Step 3: Implement; route `teach` to `TeachScreen`; add `en.teach`**

`en.teach.progress(set, of)` renders `Set <set> of <of>` (§5.2).

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): teach the classifier from past composite sets"
```

---

### Task 16: The Settings screen

**Files:**

- Create: `frontend/src/features/settings/SettingsScreen.tsx`,
  `frontend/src/features/settings/FlipTable.tsx`
- Modify: `frontend/src/App.tsx` (route `settings`), `frontend/src/strings/en.ts` (section
  `settings`)
- Test: `frontend/src/features/settings/SettingsScreen.test.tsx`

**Interfaces:**

- Consumes: `useSettings`, `putSettings`, `useClassifier`, `retrain`, `useJob`, `queryKeys`,
  `ShootingOrderPicker`, `ModelConsent`, `useModelStatus`, `Link`, `SLOTS`, `FLIPS`.
- Produces:
  - `SettingsScreen()`: a form over a draft of `Settings`.
    **Save** (enabled when the draft differs) sends `putSettings(draft)` as a quiet mutation and
    shows `describeError` inline on failure; **Discard changes** restores the saved settings. On
    success it invalidates everything (a new library root changes every view). Fields and
    bounds, which come from `config.Settings`:

    | Field | Control |
    |---|---|
    | `library_root` | text; an absolute path. Help: changing it does not move existing cases |
    | `jpeg_quality` | number 50–100 |
    | `strip_gps` | checkbox |
    | `cell_size` | two numbers, 100–4000 each |
    | `gap_px` | number 0–200 |
    | `gap_color` | `<input type="color">` (`#rrggbb`) |
    | `shooting_order` | `ShootingOrderPicker` |
    | `flip_start`, `flip_pins` | `FlipTable` |
    | `cache_max_gb` | number, above 0 and at most 100, step 0.5 |
    | `idle_shutdown_minutes` | number 0–1440; help: 0 never shuts down (`IdleWatch`) |

    An AI section below the form: `ModelConsent` while the model is unavailable, else a line
    that it is downloaded. Then the classifier: trained or not, the count per slot of
    `min_samples_per_slot`, `stale_samples` with the advice to confirm a few composites again
    (§4.6), and `damaged` / `flips_damaged` when set. Then **Retrain now** (`retrain()`,
    following the job, then invalidating `classifier`) and **Teach from past composites** (a
    `Link`).
  - `FlipTable(props: { settings: Settings; status: ClassifierView["flips"]; onChange: (next:
    Pick<Settings, "flip_start" | "flip_pins">) => void })`: one row per slot with the start
    value (a `<select>` → `flip_start[slot]`), the learned value and its confirmations (or "—"),
    the effective value, and **Pin**. Checking **Pin** stores `flip_pins[slot] = <the effective
    value>`, with a `<select>` to change it; unchecking deletes the key. Learning stops for a
    pinned slot (§4.7.2).

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `saving sends the whole settings with the change` | set JPEG quality to 85 → **Save** → `PUT /api/settings` body equals the loaded settings with `jpeg_quality: 85` |
| `pinning a slot stores its flip, unpinning removes it` | **Pin** on Upper occlusal → body `flip_pins` `{ upper_occlusal: "vertical" }`; unpin and save → `flip_pins` `{}` |
| `a refused value shows the server's reason` | `PUT` → `errorReply(422, "invalid_request", "The request is not valid.", [{ loc: ["body", "library_root"], msg: "Value error, library_root must be an absolute path" }])` → that text inline |
| `retrain starts a job` | **Retrain now** → `POST /api/classifier/retrain`; on `done` → `GET /api/classifier/status` again |
| `a damaged flip history is explained` | `flips_damaged: "The flip history is damaged…"` → shown |
| `zero minutes means never` | the idle field's help text is shown |

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- src/features/settings`
Expected: FAIL (unresolved import).

- [ ] **Step 3: Implement; route `settings` to `SettingsScreen`; add `en.settings`**

- [ ] **Step 4: Run the tests and all gates**

Run: `npm --prefix frontend run check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): add the settings screen with learned flips, the model, and the classifier"
```

---

### Task 17: README and an end-to-end check in a real browser

**Files:**

- Modify: `README.md`
- Fixes found by the check, each with a test, in the files they concern.

**Interfaces:**

- Consumes: everything above; `tests/support/app.py` (`procedure_set`, `view_set`) for photos.
- Produces: no new interface.

- [ ] **Step 1: Update the README**

- The status line: the app UI is built; distribution (install script, launcher, in-app update)
  comes next.
- Under Development:
  - `uv sync`, then `uv run dio serve --no-browser`;
  - `npm --prefix frontend ci`, then `npm --prefix frontend run dev`, and open
    `http://localhost:5173` (not `127.0.0.1`, amendment 1);
  - `npm --prefix frontend run build` puts the UI into the package, so `dio serve` serves it at
    `http://127.0.0.1:8765`;
  - `npm --prefix frontend run gen:api` after an API change, and
    `uv run python tests/support/transform_fixture.py` after a `core.transform` change.

- [ ] **Step 2: Build and start a throw-away instance**

Run:

```powershell
npm --prefix frontend run build
$env:DIO_APP_DIR = Join-Path $env:TEMP "dio-e2e"
uv run dio serve --no-browser
```

Expected: `http://127.0.0.1:8765` shows the Library.

- [ ] **Step 3: Write synthetic photos to a scratch folder**

Run this from the repository root in a second PowerShell (the photos are synthetic, and they go
to `%TEMP%`, outside the repository):

```powershell
uv run python -c "import os, sys; sys.path.insert(0, 'tests'); from pathlib import Path; from support.app import procedure_set, view_set; out = Path(os.environ['TEMP'], 'dio-e2e-photos'); (out / 'composite').mkdir(parents=True, exist_ok=True); s = procedure_set(); [(out / n).write_bytes(b) for n, b in (('reference.jpg', s.reference), ('intra.jpg', s.intra), ('post.jpg', s.post))]; [(out / 'composite' / n).write_bytes(b) for n, b in view_set(count=10)]"
```

Add an empty `IMG_0001.MOV` and a `.DS_Store` to `composite/`.

- [ ] **Step 4: Walk through the workflows**

Use a browser (the Playwright tools, or by hand) at `http://127.0.0.1:8765`. Check each item and
note any failure:

1. Create a procedure case for a new patient; drop `reference.jpg` → Locked.
2. Drop `intra.jpg` and `post.jpg` → processing, then a confidence badge each; the stages are
   intra-op and post-op.
3. Open **Compare**: onion skin, split, then **Align** → drag, wheel, `[`/`]`, arrow keys; the
   processed image updates after each gesture. Two points and four points; **Reset
   perspective**; **Reset to auto**.
4. Sequence: reorder, save, preview. Export with **Keep both**, then again with **Replace
   previous export**; **Reveal in Finder** opens Explorer on the export folder; **Download zip**
   downloads.
5. Create a composite case for the same patient (the name suggestion offers them); drop the
   `composite` folder → one summary "Skipped 1 file: 1 video" (no word about `.DS_Store`); the
   tenth photo is in the tray.
6. Move a photo with the mouse and with **Move to**; open a cell with Enter; pan, flip, reset;
   change the baseline; export.
7. Library: the cards' counts; search by part of the name; **Export all** for the patient.
8. Teach: if the model is not downloaded, the consent shows (decline it: no download in this
   check). Settings: change and save the JPEG quality; pin a flip.
9. **Quit** → the closed notice. Start `uv run dio serve --no-browser` again → the page reloads
   by itself.
10. Keyboard only: Tab through the Library and a composite case; every control shows its focus
    ring.

- [ ] **Step 5: Fix what failed**

For each failure: a test that reproduces it in the owning feature, the fix, the gates, and a
commit of its own (`fix(ui): …`).

- [ ] **Step 6: Run every gate**

Run: `npm --prefix frontend run check`, `npm --prefix frontend run gen:api` (then `git status`
shows no change), and the five Python gates.
Expected: all pass.

- [ ] **Step 7: Commit the README**

```bash
git add README.md
git commit -m "docs: describe the app UI and how to develop it"
```

- [ ] **Step 8: Push and open the pull request (confirm with your human partner first)**

```bash
git push -u origin feat/frontend
gh pr create --title "Plan 4: React frontend" --body "Implements doc/superpowers/plans/2026-10-25-plan-4-frontend.md."
```
