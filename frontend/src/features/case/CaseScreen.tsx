import { ApiError, describeError } from "../../api/http";
import { useCase } from "../../api/queries";
import { Button } from "../../components/Button";
import { Link } from "../../lib/router";
import { fill } from "../../lib/text";
import { en } from "../../strings/en";
import { ProcedureScreen } from "../procedure/ProcedureScreen";

const LIBRARY = { name: "library" } as const;

export function CaseScreen({ caseId }: { caseId: string }) {
  const query = useCase(caseId);

  if (query.data !== undefined) {
    // Until Task 13 a composite case only shows its title.
    if (query.data.case.type === "composite") {
      return (
        <h1 className="text-xl font-semibold">
          {fill(en.case.compositeTitle, { title: query.data.case.title })}
        </h1>
      );
    }
    return <ProcedureScreen view={query.data} />;
  }

  if (query.isError) {
    if (query.error instanceof ApiError && query.error.code === "case_not_found") {
      return (
        <div className="flex flex-col gap-2">
          <p role="alert">{en.case.missing}</p>
          <Link to={LIBRARY} className="text-sky-800 underline">
            {en.app.backToLibrary}
          </Link>
        </div>
      );
    }
    return (
      <div role="alert" className="flex items-center gap-3 text-sm text-red-900">
        <p>{fill(en.case.loadFailed, { message: describeError(query.error) })}</p>
        <Button
          onClick={() => {
            void query.refetch();
          }}
        >
          {en.case.retry}
        </Button>
      </div>
    );
  }

  return (
    <p role="status" className="text-sm text-slate-700">
      {en.case.loading}
    </p>
  );
}
