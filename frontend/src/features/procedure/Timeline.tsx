import type { CaseView } from "../../api/types";
import { en } from "../../strings/en";
import { groupByStage } from "./order";
import { PhotoTile } from "./PhotoTile";

export function Timeline(props: { view: CaseView; onCompare: (photoId: string) => void }) {
  const { view, onCompare } = props;
  const groups = groupByStage(view.case.photos);

  if (groups.length === 0) {
    return <p className="text-sm text-slate-700">{en.procedure.timeline.empty}</p>;
  }
  return (
    <div className="flex flex-col gap-6">
      {groups.map(({ stage, photos }) => {
        const key = stage ?? "unsorted";
        const headingId = `stage-${key}`;
        return (
          <section key={key} aria-labelledby={headingId} className="flex flex-col gap-2">
            <h3 id={headingId} className="text-base font-semibold text-slate-900">
              {en.stages[key]}
            </h3>
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-3">
              {photos.map((photo) => (
                <PhotoTile key={photo.id} view={view} photo={photo} onCompare={onCompare} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
