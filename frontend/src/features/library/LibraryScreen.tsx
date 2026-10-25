import { useState } from "react";
import { describeError } from "../../api/http";
import { usePatients } from "../../api/queries";
import type { CaseType, PatientView } from "../../api/types";
import { Button } from "../../components/Button";
import { matchesPatient } from "../../lib/text";
import { en } from "../../strings/en";
import { ExportDialog } from "../export/ExportDialog";
import { CaseCard } from "./CaseCard";
import { ImportArchiveButton } from "./ImportArchiveButton";
import { NewCaseDialog } from "./NewCaseDialog";
import { PatientEditDialog } from "./PatientEditDialog";

// The patient is kept by id so a refetch while the dialog is open shows the current record.
type Open =
  | { kind: "newCase"; type: CaseType }
  | { kind: "edit"; patientId: string }
  | { kind: "export"; patientId: string };

export function LibraryScreen() {
  const patients = usePatients();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Open | null>(null);

  const all = patients.data?.patients ?? [];
  const shown = all.filter((patient) => matchesPatient(patient, query));
  const close = () => {
    setOpen(null);
  };

  const dialogPatient =
    open !== null && open.kind !== "newCase"
      ? all.find((patient) => patient.id === open.patientId)
      : undefined;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">{en.nav.library}</h1>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          aria-label={en.library.search}
          placeholder={en.library.search}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
          className="min-w-56 flex-1 rounded-md border border-slate-400 bg-white px-2 py-1.5 text-sm text-slate-900"
        />
        <Button
          variant="primary"
          onClick={() => {
            setOpen({ kind: "newCase", type: "procedure" });
          }}
        >
          {en.library.newProcedure}
        </Button>
        <Button
          variant="primary"
          onClick={() => {
            setOpen({ kind: "newCase", type: "composite" });
          }}
        >
          {en.library.newComposite}
        </Button>
        <ImportArchiveButton />
      </div>

      {patients.isError && (
        <div role="alert" className="flex items-center gap-3 text-sm text-red-900">
          <p>{en.library.loadFailed.replace("{message}", describeError(patients.error))}</p>
          <Button
            onClick={() => {
              void patients.refetch();
            }}
          >
            {en.library.retry}
          </Button>
        </div>
      )}
      {patients.isPending && (
        <p role="status" className="text-sm text-slate-700">
          {en.library.loading}
        </p>
      )}

      {patients.data !== undefined && patients.data.problems.length > 0 && (
        <section
          aria-labelledby="library-problems"
          className="rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950"
        >
          <h2 id="library-problems" className="mb-1 font-medium">
            {en.library.problemsTitle}
          </h2>
          <ul className="flex flex-col gap-1">
            {patients.data.problems.map((problem) => (
              <li key={`${problem.folder}\n${problem.message}`}>
                <span className="font-medium break-all">{problem.folder}</span>{" "}
                <span>{problem.message}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {patients.data !== undefined && all.length === 0 && (
        <p className="text-sm text-slate-700">{en.library.empty}</p>
      )}
      {all.length > 0 && shown.length === 0 && (
        <p className="text-sm text-slate-700">{en.library.noMatch}</p>
      )}

      {shown.map((patient) => (
        <PatientSection key={patient.id} patient={patient} onOpen={setOpen} />
      ))}

      {open?.kind === "newCase" && (
        <NewCaseDialog type={open.type} patients={all} onClose={close} />
      )}
      {open?.kind === "edit" && dialogPatient !== undefined && (
        <PatientEditDialog patient={dialogPatient} onClose={close} />
      )}
      {open?.kind === "export" && dialogPatient !== undefined && (
        <ExportDialog
          target={{
            kind: "patient",
            patientId: dialogPatient.id,
            folder: dialogPatient.folder,
            cases: dialogPatient.cases.map(({ id, title }) => ({ id, title })),
          }}
          onClose={close}
        />
      )}
    </div>
  );
}

function PatientSection(props: { patient: PatientView; onOpen: (open: Open) => void }) {
  const { patient, onOpen } = props;
  const headingId = `patient-${patient.id}`;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id={headingId} className="text-lg font-semibold text-slate-900">
            {patient.name}
          </h2>
          {patient.note !== null && patient.note !== "" && (
            <p className="text-sm text-slate-700">{patient.note}</p>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            aria-describedby={headingId}
            onClick={() => {
              onOpen({ kind: "edit", patientId: patient.id });
            }}
          >
            {en.library.edit}
          </Button>
          <Button
            aria-describedby={headingId}
            onClick={() => {
              onOpen({ kind: "export", patientId: patient.id });
            }}
          >
            {en.library.exportAll}
          </Button>
        </div>
      </div>
      {patient.cases.length === 0 ? (
        <p className="text-sm text-slate-700">{en.library.noCases}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-3">
          {patient.cases.map((summary) => (
            <CaseCard key={summary.id} summary={summary} />
          ))}
        </div>
      )}
    </section>
  );
}
