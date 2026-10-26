import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type SubmitEvent } from "react";
import { patchPatient } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import type { PatientView } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { en } from "../../strings/en";

const fieldClass = "rounded-md border border-slate-400 bg-white px-2 py-1.5 text-sm text-slate-900";

export function PatientEditDialog(props: { patient: PatientView; onClose: () => void }) {
  const { patient, onClose } = props;
  const queryClient = useQueryClient();
  const nameId = useId();
  const noteId = useId();
  const [name, setName] = useState(patient.name);
  const [note, setNote] = useState(patient.note ?? "");
  // A ref, not `isPending`: a double Enter can submit before React renders the disabled button.
  const requested = useRef(false);

  const save = useMutation({
    mutationFn: (patch: { name: string; note: string | null }) => patchPatient(patient.id, patch),
    onSuccess: () => {
      // A rename moves the patient's folder, so every cached case shows a stale path.
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients });
      void queryClient.invalidateQueries({ queryKey: ["case"] });
      onClose();
    },
    onError: () => {
      requested.current = false;
    },
  });

  const trimmedName = name.trim();

  const onSubmit = (event: SubmitEvent) => {
    event.preventDefault();
    if (requested.current || trimmedName === "") return;
    requested.current = true;
    // `null`, not an omitted field: only an explicit null clears a stored note.
    save.mutate({ name: trimmedName, note: note.trim() === "" ? null : note.trim() });
  };

  return (
    <Dialog title={en.library.editPatient.title} onClose={onClose}>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={nameId} className="text-sm font-medium text-slate-900">
            {en.library.editPatient.name}
          </label>
          <input
            id={nameId}
            className={fieldClass}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={noteId} className="text-sm font-medium text-slate-900">
            {en.library.editPatient.note}
          </label>
          <input
            id={noteId}
            className={fieldClass}
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
            }}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>{en.common.cancel}</Button>
          <Button type="submit" variant="primary" disabled={trimmedName === "" || save.isPending}>
            {save.isPending ? en.library.editPatient.saving : en.library.editPatient.save}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
