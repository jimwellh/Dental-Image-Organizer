import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { deletePatient } from "../../api/endpoints";
import { queryKeys } from "../../api/queries";
import type { PatientView } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

export function PatientDeleteDialog(props: { patient: PatientView; onClose: () => void }) {
  const { patient, onClose } = props;
  const queryClient = useQueryClient();
  // A ref, not `isPending`: a double click on the confirm button must send one request.
  const requested = useRef(false);

  const remove = useMutation({
    mutationFn: () => deletePatient(patient.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients });
      // Removed, not invalidated: the case screen shows cached data over a failed refetch, so
      // browser Back would bring a deleted case back on screen (as CaseHeader does on delete).
      for (const summary of patient.cases) {
        queryClient.removeQueries({ queryKey: queryKeys.case(summary.id) });
      }
      onClose();
    },
    onError: () => {
      requested.current = false;
    },
  });

  const count = patient.cases.length;
  const { body } = en.library.deletePatient;
  const form = count === 0 ? body.none : count === 1 ? body.one : body.other;

  return (
    <ConfirmDialog
      title={en.library.deletePatient.title}
      body={fill(form, { name: patient.name, n: String(count) })}
      confirmLabel={en.library.deletePatient.confirm}
      onCancel={onClose}
      onConfirm={() => {
        if (requested.current) return;
        requested.current = true;
        remove.mutate();
      }}
    />
  );
}
