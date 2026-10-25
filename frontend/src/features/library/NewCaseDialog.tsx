import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type SubmitEvent } from "react";
import { createCase } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import type { CaseCreate, CaseType, PatientView } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { navigate } from "../../lib/router";
import { foldForSearch } from "../../lib/text";
import { en } from "../../strings/en";

const MAX_SUGGESTIONS = 8;

const fieldClass = "rounded-md border border-slate-400 bg-white px-2 py-1.5 text-sm text-slate-900";

export function NewCaseDialog(props: {
  type: CaseType;
  patients: readonly PatientView[];
  onClose: () => void;
}) {
  const { type, patients, onClose } = props;
  const queryClient = useQueryClient();
  const ids = { name: useId(), title: useId(), note: useId(), clash: useId() };
  const nameField = useRef<HTMLInputElement>(null);
  const clashHeading = useRef<HTMLParagraphElement>(null);

  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState<PatientView | null>(null);
  // The patients that already carry the typed name; set once the user submitted without a pick.
  const [clash, setClash] = useState<readonly PatientView[] | null>(null);
  const [newNote, setNewNote] = useState("");
  // A ref, not `isPending`: the second click of a double click can arrive before React has
  // rendered the disabled button, and a second request would create a second case.
  const requested = useRef(false);

  const create = useMutation({
    mutationFn: createCase,
    onSuccess: (view) => {
      queryClient.setQueryData(queryKeys.case(view.case.id), view);
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients });
      onClose();
      navigate({ name: "case", caseId: view.case.id });
    },
    onError: () => {
      // The global handler tells the user; the dialog stays for another try.
      requested.current = false;
    },
  });

  useEffect(() => {
    // The submit button that had the focus is gone once the choice replaces the form.
    if (clash !== null) clashHeading.current?.focus();
  }, [clash]);

  const send = (body: CaseCreate) => {
    if (requested.current) return;
    requested.current = true;
    create.mutate(body);
  };

  const typedName = name.trim();
  const folded = foldForSearch(name);
  const suggestions =
    picked === null && folded !== ""
      ? patients
          .filter((patient) => foldForSearch(patient.name).includes(folded))
          .slice(0, MAX_SUGGESTIONS)
      : [];

  const onSubmit = (event: SubmitEvent) => {
    event.preventDefault();
    const caseTitle = title.trim();
    if (typedName === "" || caseTitle === "") return;
    if (picked !== null) {
      send({ type, title: caseTitle, patient_id: picked.id });
      return;
    }
    // The server always makes a new patient from a name, and §3.2 lets two patients share one, so
    // a name that exists must be a choice and never a silent duplicate.
    const same = patients.filter((patient) => foldForSearch(patient.name) === folded);
    if (same.length > 0) setClash(same);
    else send({ type, title: caseTitle, patient_name: typedName });
  };

  const dialogTitle =
    type === "procedure" ? en.library.newCase.titleProcedure : en.library.newCase.titleComposite;

  if (clash !== null) {
    const caseTitle = title.trim();
    const note = newNote.trim();
    return (
      <Dialog title={dialogTitle} onClose={onClose}>
        <div className="flex flex-col gap-4">
          <p ref={clashHeading} tabIndex={-1} className="text-sm text-slate-900">
            {en.library.newCase.sameName.replace("{name}", typedName)}
          </p>
          <ul className="flex flex-col gap-2">
            {clash.map((patient) => (
              <li
                key={patient.id}
                className="flex items-center justify-between gap-3 rounded-md border border-slate-300 p-2"
              >
                <span id={`${ids.clash}-${patient.id}`} className="text-sm text-slate-900">
                  {patient.name}
                  <span className="block text-slate-700">
                    {patient.note ?? en.library.newCase.noNote}
                  </span>
                </span>
                <Button
                  aria-describedby={`${ids.clash}-${patient.id}`}
                  disabled={create.isPending}
                  onClick={() => {
                    send({ type, title: caseTitle, patient_id: patient.id });
                  }}
                >
                  {en.library.newCase.usePatient}
                </Button>
              </li>
            ))}
          </ul>
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.note} className="text-sm font-medium text-slate-900">
              {en.library.newCase.newPatientNote}
            </label>
            <input
              id={ids.note}
              className={fieldClass}
              value={newNote}
              onChange={(event) => {
                setNewNote(event.target.value);
              }}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button onClick={onClose}>{en.common.cancel}</Button>
            <Button
              variant="primary"
              disabled={create.isPending}
              onClick={() => {
                send({
                  type,
                  title: caseTitle,
                  patient_name: typedName,
                  ...(note === "" ? {} : { patient_note: note }),
                });
              }}
            >
              {en.library.newCase.newPatient}
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title={dialogTitle} onClose={onClose}>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.name} className="text-sm font-medium text-slate-900">
            {en.library.newCase.patientName}
          </label>
          <input
            id={ids.name}
            ref={nameField}
            className={fieldClass}
            autoComplete="off"
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              // Typing again is a new decision, not a continued pick.
              setPicked(null);
            }}
          />
          {suggestions.length > 0 && (
            <ul aria-label={en.library.newCase.suggestions} className="flex flex-col gap-1">
              {suggestions.map((patient) => (
                <li key={patient.id}>
                  <button
                    type="button"
                    className="w-full rounded-md border border-slate-300 px-2 py-1 text-left text-sm text-slate-900 hover:bg-slate-100"
                    onClick={() => {
                      setPicked(patient);
                      setName(patient.name);
                      nameField.current?.focus();
                    }}
                  >
                    <span>{patient.name}</span>
                    {patient.note !== null && (
                      <span className="block text-slate-700">{patient.note}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {picked !== null && (
            <p className="text-sm text-slate-700">{en.library.newCase.usingPatient}</p>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.title} className="text-sm font-medium text-slate-900">
            {type === "procedure"
              ? en.library.newCase.procedureLabel
              : en.library.newCase.compositeLabel}
          </label>
          <input
            id={ids.title}
            className={fieldClass}
            value={title}
            onChange={(event) => {
              setTitle(event.target.value);
            }}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{en.common.cancel}</Button>
          <Button
            type="submit"
            variant="primary"
            disabled={typedName === "" || title.trim() === "" || create.isPending}
          >
            {create.isPending ? en.library.newCase.creating : en.library.newCase.create}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
