import { caseThumbUrl } from "../../api/images";
import type { CaseSummary } from "../../api/types";
import { Badge } from "../../components/Badge";
import { Link } from "../../lib/router";
import { en } from "../../strings/en";

const MAX_THUMBNAILS = 4;

const count = (template: string, n: number): string => template.replace("{n}", String(n));

export function CaseCard({ summary }: { summary: CaseSummary }) {
  const created = new Date(summary.created_at);
  return (
    <Link
      to={{ name: "case", caseId: summary.id }}
      className="flex flex-col gap-2 rounded-lg border border-slate-300 bg-white p-3 hover:border-sky-700"
    >
      {/* The title comes first so it leads the card's accessible name; the pictures are decoration. */}
      <span className="font-medium text-slate-900">{summary.title}</span>
      <span className="text-xs text-slate-700">
        {en.library.caseType[summary.type]} ·{" "}
        <time dateTime={summary.created_at}>
          {created.toLocaleDateString(undefined, { dateStyle: "medium" })}
        </time>
      </span>
      <span className="grid grid-cols-4 gap-1">
        {summary.thumbnails.slice(0, MAX_THUMBNAILS).map((photoId) => (
          <img
            key={photoId}
            src={caseThumbUrl(summary, photoId)}
            alt=""
            loading="lazy"
            className="aspect-[4/3] w-full rounded bg-slate-200 object-cover"
          />
        ))}
      </span>
      <span className="flex flex-wrap gap-1">
        {summary.needs_review > 0 && (
          <Badge tone="warn">{count(en.library.badges.review, summary.needs_review)}</Badge>
        )}
        {summary.pending > 0 && (
          <Badge tone="neutral">{count(en.library.badges.pending, summary.pending)}</Badge>
        )}
        {summary.failed > 0 && (
          <Badge tone="error">{count(en.library.badges.failed, summary.failed)}</Badge>
        )}
      </span>
    </Link>
  );
}
