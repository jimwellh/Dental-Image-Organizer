import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type SubmitEvent } from "react";
import { caseArchiveUrl, deleteCase, patchCase, reveal } from "../../api/endpoints";
import { queryKeys, useCaseChange } from "../../api/queries";
import type { CaseView } from "../../api/types";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { navigate } from "../../lib/router";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";

const linkClass =
  "inline-block rounded-md border border-slate-400 bg-white px-3 py-1.5 text-sm font-medium text-slate-900 hover:bg-slate-100";

function TitleEditor(props: { view: CaseView; onDone: () => void }) {
  const { view, onDone } = props;
  const caseId = view.case.id;
  const inputId = useId();
  const [title, setTitle] = useState(view.case.title);
  const rename = useCaseChange(caseId, (next: string) => patchCase(caseId, { title: next }));
  const trimmed = title.trim();

  const onSubmit = (event: SubmitEvent) => {
    event.preventDefault();
    if (rename.isPending || trimmed === "") return;
    if (trimmed === view.case.title) {
      onDone();
      return;
    }
    rename.mutate(trimmed, { onSuccess: onDone });
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2">
      <label htmlFor={inputId} className="sr-only">
        {en.case.header.titleLabel}
      </label>
      <input
        id={inputId}
        autoFocus
        value={title}
        onChange={(event) => {
          setTitle(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") onDone();
        }}
        className="min-w-64 rounded-md border border-slate-400 bg-white px-2 py-1.5 text-xl font-semibold text-slate-900"
      />
      <Button type="submit" variant="primary" disabled={trimmed === "" || rename.isPending}>
        {en.case.header.save}
      </Button>
      <Button onClick={onDone}>{en.case.header.cancel}</Button>
    </form>
  );
}

export function CaseHeader(props: { view: CaseView; onExport: () => void }) {
  const { view, onExport } = props;
  const caseId = view.case.id;
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // A ref, not `isPending`: a double click on the confirm button must send one request.
  const deleting = useRef(false);

  const reveals = useMutation({ mutationFn: reveal });
  const remove = useMutation({
    mutationFn: () => deleteCase(caseId),
    onSuccess: () => {
      navigate({ name: "library" });
      // After leaving: removing the case while its screen is mounted would refetch a missing case.
      queryClient.removeQueries({ queryKey: queryKeys.case(caseId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients });
    },
    onError: () => {
      deleting.current = false;
    },
  });

  const { patient } = view;

  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          {renaming ? (
            <TitleEditor
              view={view}
              onDone={() => {
                setRenaming(false);
              }}
            />
          ) : (
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold text-slate-900">{view.case.title}</h1>
              <Button
                onClick={() => {
                  setRenaming(true);
                }}
              >
                {en.case.header.rename}
              </Button>
            </div>
          )}
          <p className="text-sm text-slate-800">{patient.name}</p>
          {patient.note !== null && patient.note !== "" && (
            <p className="text-sm text-slate-700">{patient.note}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={onExport}>
            {en.case.header.export}
          </Button>
          <Button
            disabled={reveals.isPending}
            onClick={() => {
              reveals.mutate(view.folder);
            }}
          >
            {en.case.header.reveal}
          </Button>
          <a href={caseArchiveUrl(caseId)} download className={linkClass}>
            {en.case.header.download}
          </a>
          <Button
            onClick={() => {
              setConfirmingDelete(true);
            }}
          >
            {en.case.header.delete}
          </Button>
        </div>
      </div>
      {confirmingDelete && (
        <ConfirmDialog
          title={en.case.header.deleteTitle}
          body={fill(en.case.header.deleteBody, { title: view.case.title })}
          confirmLabel={en.case.header.deleteConfirm}
          onCancel={() => {
            setConfirmingDelete(false);
          }}
          onConfirm={() => {
            if (deleting.current) return;
            deleting.current = true;
            remove.mutate();
          }}
        />
      )}
    </header>
  );
}
