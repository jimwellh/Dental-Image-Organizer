import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState, type ReactNode } from "react";
import { putSettings, retrain } from "../../api/endpoints";
import { describeError } from "../../api/http";
import { isFinished, useJob } from "../../api/jobs";
import { queryKeys, useClassifier, useModelStatus, useSettings } from "../../api/queries";
import type { ClassifierView, Settings } from "../../api/types";
import { Button } from "../../components/Button";
import { ShootingOrderPicker } from "../../components/ShootingOrderPicker";
import { Link } from "../../lib/router";
import { SLOTS } from "../../lib/slots";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { ModelConsent } from "../ai/ModelConsent";
import { FlipTable } from "./FlipTable";

// The server's bounds (`config.Settings`), repeated so a bad value is caught before it is sent.
interface Bounds {
  min: number;
  max: number;
  integer: boolean;
  /** `cache_max_gb` must be above its minimum, not at it. */
  minExclusive?: boolean;
}

const JPEG_QUALITY: Bounds = { min: 50, max: 100, integer: true };
const CELL_SIDE: Bounds = { min: 100, max: 4000, integer: true };
const GAP_PX: Bounds = { min: 0, max: 200, integer: true };
const CACHE_GB: Bounds = { min: 0, max: 100, integer: false, minExclusive: true };
const IDLE_MINUTES: Bounds = { min: 0, max: 1440, integer: true };

function within(value: number, bounds: Bounds): boolean {
  // NaN, which an emptied field reports, fails every comparison.
  if (bounds.integer && !Number.isInteger(value)) return false;
  const aboveMin = bounds.minExclusive === true ? value > bounds.min : value >= bounds.min;
  return aboveMin && value <= bounds.max;
}

function isValid(draft: Settings): boolean {
  return (
    within(draft.jpeg_quality, JPEG_QUALITY) &&
    draft.cell_size.every((side) => within(side, CELL_SIDE)) &&
    within(draft.gap_px, GAP_PX) &&
    within(draft.cache_max_gb, CACHE_GB) &&
    within(draft.idle_shutdown_minutes, IDLE_MINUTES)
  );
}

// Both sides come from the same parse, so key order matches and a string compare is exact.
const sameSettings = (a: Settings, b: Settings): boolean => JSON.stringify(a) === JSON.stringify(b);

const inputClass = "rounded-md border border-slate-400 bg-white px-2 py-1 text-sm text-slate-900";

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: (props: { id: string; describedBy: string | undefined }) => ReactNode;
}) {
  const id = useId();
  const helpId = `${id}-help`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-slate-900">
        {label}
      </label>
      {children({ id, describedBy: help === undefined ? undefined : helpId })}
      {help !== undefined && (
        <p id={helpId} className="text-sm text-slate-700">
          {help}
        </p>
      )}
    </div>
  );
}

interface NumberFieldProps {
  label: string;
  help?: string;
  value: number;
  bounds: Bounds;
  step?: number | "any";
  onChange: (value: number) => void;
}

/**
 * Keeps what the user typed, so a half-typed or emptied field is not rewritten under the cursor.
 * An unusable text reports `NaN`, which the screen treats as out of bounds.
 */
function NumberField({ label, help, value, bounds, step = 1, onChange }: NumberFieldProps) {
  const [text, setText] = useState(String(value));
  const [seen, setSeen] = useState(value);
  // A value that changed from outside (Discard) replaces the text; one this field sent does not.
  if (!Object.is(value, seen)) {
    setSeen(value);
    setText(String(value));
  }
  return (
    <Field label={label} help={help}>
      {({ id, describedBy }) => (
        <input
          id={id}
          type="number"
          inputMode={bounds.integer ? "numeric" : "decimal"}
          min={bounds.min}
          max={bounds.max}
          step={step}
          value={text}
          aria-describedby={describedBy}
          aria-invalid={!within(value, bounds)}
          className={`w-32 ${inputClass}`}
          onChange={(event) => {
            const raw = event.target.value;
            const next = raw.trim() === "" ? Number.NaN : Number(raw);
            setText(raw);
            setSeen(next);
            onChange(next);
          }}
        />
      )}
    </Field>
  );
}

function Classifier({ status }: { status: ClassifierView }) {
  const client = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: retrain,
    onSuccess: ({ job_id }) => {
      setJobId(job_id);
    },
  });
  const job = useJob(jobId);
  const finished = job !== null && isFinished(job);

  useEffect(() => {
    if (finished) void client.invalidateQueries({ queryKey: queryKeys.classifier });
  }, [finished, client]);

  const running = start.isPending || (jobId !== null && !finished);
  const failure = job?.state === "failed" ? (job.error?.message ?? job.message ?? "") : null;

  return (
    <div className="flex flex-col gap-3 text-sm text-slate-900">
      <p className="font-medium">
        {status.trained ? en.settings.ai.trained : en.settings.ai.untrained}
      </p>
      <ul className="grid max-w-xl grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-3">
        {SLOTS.map((slot) => (
          <li key={slot} className="tabular-nums">
            {fill(en.teach.count, {
              slot: en.slots[slot],
              n: String(status.counts[slot] ?? 0),
              of: String(status.min_samples_per_slot),
            })}
          </li>
        ))}
      </ul>
      {status.stale_samples > 0 && (
        <p>{fill(en.settings.ai.stale, { n: String(status.stale_samples) })}</p>
      )}
      {status.damaged !== null && (
        <p role="alert" className="text-red-900">
          {status.damaged}
        </p>
      )}
      {status.flips_damaged !== null && (
        <p role="alert" className="text-red-900">
          {status.flips_damaged}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={running}
          onClick={() => {
            start.mutate();
          }}
        >
          {en.settings.ai.retrain}
        </Button>
        <Link to={{ name: "teach" }} className="text-sky-800 underline">
          {en.ai.teach}
        </Link>
      </div>
      {running && <p role="status">{en.settings.ai.retraining}</p>}
      {job?.state === "done" && (
        <p role="status">
          {job.result?.trained === true ? en.settings.ai.retrained : en.settings.ai.notEnough}
        </p>
      )}
      {start.isError && (
        <p role="alert" className="text-red-900">
          {fill(en.settings.ai.retrainFailed, { message: describeError(start.error) })}
        </p>
      )}
      {failure !== null && (
        <p role="alert" className="text-red-900">
          {fill(en.settings.ai.retrainFailed, { message: failure })}
        </p>
      )}
    </div>
  );
}

// The classifier arrives as a prop: a second `useClassifier` observer mounting later would refetch it.
function AiSection({ classifier }: { classifier: ClassifierView | undefined }) {
  const model = useModelStatus();
  return (
    <section className="flex flex-col gap-3" aria-labelledby="settings-ai">
      <h2 id="settings-ai" className="text-lg font-semibold text-slate-900">
        {en.settings.ai.title}
      </h2>
      {model.data?.available === true ? (
        <p className="text-sm text-slate-900">{en.settings.ai.downloaded}</p>
      ) : (
        <ModelConsent />
      )}
      {classifier !== undefined && <Classifier status={classifier} />}
    </section>
  );
}

export function SettingsScreen() {
  const client = useQueryClient();
  const settings = useSettings();
  const classifier = useClassifier();
  // `null` until the user changes something; the form then shows the saved settings.
  const [edited, setEdited] = useState<Settings | null>(null);

  const save = useMutation({
    mutationFn: putSettings,
    // The error is shown beside the Save button, not in the global notice.
    meta: { quiet: true },
    onSuccess: async (saved) => {
      client.setQueryData(queryKeys.settings, saved);
      setEdited(null);
      // A new library root changes every view, so nothing cached can be trusted.
      await client.invalidateQueries();
    },
  });

  if (settings.isError) {
    return (
      <p role="alert" className="text-red-900">
        {en.settings.loadFailed}
      </p>
    );
  }
  if (settings.data === undefined) return <p role="status">{en.settings.loading}</p>;

  const saved = settings.data;
  const draft = edited ?? saved;
  const dirty = edited !== null && !sameSettings(edited, saved);

  const update = (changes: Partial<Settings>) => {
    save.reset();
    setEdited({ ...draft, ...changes });
  };

  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <h1 className="text-xl font-semibold text-slate-900">{en.settings.title}</h1>
      <form
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          if (dirty && isValid(draft) && !save.isPending) save.mutate(draft);
        }}
      >
        <Field label={en.settings.libraryRoot.label} help={en.settings.libraryRoot.help}>
          {({ id, describedBy }) => (
            <input
              id={id}
              type="text"
              value={draft.library_root}
              aria-describedby={describedBy}
              className={`w-full ${inputClass}`}
              onChange={(event) => {
                update({ library_root: event.target.value });
              }}
            />
          )}
        </Field>

        <NumberField
          label={en.settings.jpegQuality.label}
          help={en.settings.jpegQuality.help}
          value={draft.jpeg_quality}
          bounds={JPEG_QUALITY}
          onChange={(jpeg_quality) => {
            update({ jpeg_quality });
          }}
        />

        <label className="flex items-center gap-2 text-sm font-medium text-slate-900">
          <input
            type="checkbox"
            checked={draft.strip_gps}
            onChange={(event) => {
              update({ strip_gps: event.target.checked });
            }}
          />
          {en.settings.stripGps.label}
        </label>

        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium text-slate-900">
            {en.settings.cellSize.legend}
          </legend>
          <div className="flex flex-wrap gap-4">
            <NumberField
              label={en.settings.cellWidth}
              value={draft.cell_size[0]}
              bounds={CELL_SIDE}
              onChange={(width) => {
                update({ cell_size: [width, draft.cell_size[1]] });
              }}
            />
            <NumberField
              label={en.settings.cellHeight}
              value={draft.cell_size[1]}
              bounds={CELL_SIDE}
              onChange={(height) => {
                update({ cell_size: [draft.cell_size[0], height] });
              }}
            />
          </div>
          <p className="text-sm text-slate-700">{en.settings.cellSize.help}</p>
        </fieldset>

        <NumberField
          label={en.settings.gapPx.label}
          help={en.settings.gapPx.help}
          value={draft.gap_px}
          bounds={GAP_PX}
          onChange={(gap_px) => {
            update({ gap_px });
          }}
        />

        <Field label={en.settings.gapColor.label}>
          {({ id }) => (
            <input
              id={id}
              type="color"
              value={draft.gap_color}
              className="h-9 w-16 rounded-md border border-slate-400 bg-white p-1"
              onChange={(event) => {
                update({ gap_color: event.target.value });
              }}
            />
          )}
        </Field>

        <section className="flex flex-col gap-2" aria-labelledby="settings-order">
          <h2 id="settings-order" className="text-lg font-semibold text-slate-900">
            {en.teach.order.title}
          </h2>
          <p className="text-sm text-slate-800">{en.teach.order.help}</p>
          <ShootingOrderPicker
            value={draft.shooting_order}
            onChange={(shooting_order) => {
              update({ shooting_order });
            }}
          />
        </section>

        <section className="flex flex-col gap-2" aria-labelledby="settings-flips">
          <h2 id="settings-flips" className="text-lg font-semibold text-slate-900">
            {en.settings.flips.title}
          </h2>
          <p className="text-sm text-slate-800">{en.settings.flips.help}</p>
          <FlipTable settings={draft} status={classifier.data?.flips ?? {}} onChange={update} />
        </section>

        <NumberField
          label={en.settings.cacheMaxGb.label}
          help={en.settings.cacheMaxGb.help}
          value={draft.cache_max_gb}
          bounds={CACHE_GB}
          step="any"
          onChange={(cache_max_gb) => {
            update({ cache_max_gb });
          }}
        />

        <NumberField
          label={en.settings.idleShutdown.label}
          help={en.settings.idleShutdown.help}
          value={draft.idle_shutdown_minutes}
          bounds={IDLE_MINUTES}
          onChange={(idle_shutdown_minutes) => {
            update({ idle_shutdown_minutes });
          }}
        />

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            variant="primary"
            disabled={!dirty || !isValid(draft) || save.isPending}
          >
            {save.isPending ? en.settings.saving : en.settings.save}
          </Button>
          <Button
            disabled={!dirty || save.isPending}
            onClick={() => {
              save.reset();
              setEdited(null);
            }}
          >
            {en.settings.discard}
          </Button>
          {save.isSuccess && !dirty && <p role="status">{en.settings.saved}</p>}
          {save.isError && (
            <p role="alert" className="text-sm text-red-900">
              {describeError(save.error)}
            </p>
          )}
        </div>
      </form>

      <AiSection classifier={classifier.data} />
    </div>
  );
}
