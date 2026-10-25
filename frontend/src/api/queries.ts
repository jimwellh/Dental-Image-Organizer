import {
  MutationCache,
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  classifierStatus,
  getCase,
  getSettings,
  getTeachSession,
  listPatients,
  modelStatus,
} from "./endpoints";
import type { CaseView } from "./types";

export const PENDING_POLL_MS = 1500;

export const queryKeys = {
  patients: ["patients"],
  case: (caseId: string) => ["case", caseId],
  settings: ["settings"],
  classifier: ["classifier"],
  model: ["model"],
  teach: (sessionId: string) => ["teach", sessionId],
} as const;

/** `onMutationError` is the one place a failed mutation is shown, unless it sets `meta.quiet`. */
export function createQueryClient(onMutationError: (error: unknown) => void): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true } },
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.quiet) return;
        onMutationError(error);
      },
    }),
  });
}

export function hasPending(view: CaseView): boolean {
  return view.case.photos.some((photo) => photo.state === "pending");
}

export const usePatients = () => useQuery({ queryKey: queryKeys.patients, queryFn: listPatients });
export const useSettings = () => useQuery({ queryKey: queryKeys.settings, queryFn: getSettings });
export const useClassifier = () =>
  useQuery({ queryKey: queryKeys.classifier, queryFn: classifierStatus });
export const useModelStatus = () => useQuery({ queryKey: queryKeys.model, queryFn: modelStatus });

export function useCase(caseId: string) {
  return useQuery({
    queryKey: queryKeys.case(caseId),
    queryFn: () => getCase(caseId),
    // Polls the case rather than following an upload's job, so it also works after the user left
    // the case and came back, or reloaded, while photos were still processing.
    refetchInterval: (query) =>
      query.state.data !== undefined && hasPending(query.state.data) ? PENDING_POLL_MS : false,
  });
}

export function useTeachSession(sessionId: string | null) {
  return useQuery({
    queryKey: queryKeys.teach(sessionId ?? ""),
    queryFn: () => getTeachSession(sessionId ?? ""),
    enabled: sessionId !== null,
  });
}

/** A change that answers with the whole case writes it into the cache instead of refetching it. */
export function useCaseChange<A>(caseId: string, change: (args: A) => Promise<CaseView>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: change,
    onSuccess: (view) => {
      client.setQueryData(queryKeys.case(caseId), view);
      // The patient list shows each case's counts and thumbnails.
      return client.invalidateQueries({ queryKey: queryKeys.patients });
    },
  });
}
