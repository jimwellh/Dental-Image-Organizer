import { useClassifier, useModelStatus } from "../../api/queries";
import { Link } from "../../lib/router";
import { en } from "../../strings/en";
import { ModelConsent } from "./ModelConsent";

const TEACH = { name: "teach" } as const;

export function AiStatusBanner() {
  const model = useModelStatus();
  const classifier = useClassifier();

  // Nothing while either answer is unknown: a banner that flashes in and out is worse than none.
  if (model.data === undefined) return null;
  if (!model.data.available) return <ModelConsent />;
  if (classifier.data === undefined || classifier.data.trained) return null;

  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-slate-900">
      <p>{en.ai.untrained}</p>
      <Link to={TEACH} className="text-sky-800 underline">
        {en.ai.teach}
      </Link>
    </div>
  );
}
