import { useState } from "react";
import type { CaseView } from "../../api/types";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { CaseHeader } from "../case/CaseHeader";
import { ExportDialog } from "../export/ExportDialog";
import { DropZone } from "../intake/DropZone";
import { useCaseUpload } from "../intake/useCaseUpload";
import { CompareView } from "./CompareView";
import { ReferencePanel } from "./ReferencePanel";
import { Timeline } from "./Timeline";

export function ProcedureScreen({ view }: { view: CaseView }) {
  const caseId = view.case.id;
  const [exporting, setExporting] = useState(false);
  const [comparing, setComparing] = useState<string | null>(null);
  const { upload, progress } = useCaseUpload(caseId);

  return (
    <div className="flex flex-col gap-6">
      <CaseHeader
        view={view}
        onExport={() => {
          setExporting(true);
        }}
      />
      <ReferencePanel view={view} />
      <DropZone
        label={en.procedure.upload.label}
        busy={
          progress === null
            ? null
            : fill(en.procedure.upload.progress, {
                done: String(progress.done),
                total: String(progress.total),
              })
        }
        onFiles={(files, unreadable) => {
          void upload(files, unreadable);
        }}
      />
      <section aria-label={en.procedure.timeline.title} className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-slate-900">{en.procedure.timeline.title}</h2>
        <Timeline view={view} onCompare={setComparing} />
      </section>
      {comparing !== null && (
        <CompareView
          view={view}
          photoId={comparing}
          onClose={() => {
            setComparing(null);
          }}
        />
      )}
      {exporting && (
        <ExportDialog
          target={{
            kind: "case",
            caseId,
            caseType: view.case.type,
            folder: view.folder,
          }}
          onClose={() => {
            setExporting(false);
          }}
        />
      )}
    </div>
  );
}
