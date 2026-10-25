import {
  createElement,
  useMemo,
  useSyncExternalStore,
  type MouseEvent,
  type ReactNode,
} from "react";

export type Route =
  | { name: "library" }
  | { name: "case"; caseId: string }
  | { name: "teach" }
  | { name: "settings" }
  | { name: "notFound" };

const CASE_PREFIX = "/cases/";

function decodeSegment(segment: string): string | null {
  try {
    return decodeURIComponent(segment);
  } catch {
    // A malformed escape in the address bar is an unknown page, not a crash.
    return null;
  }
}

export function parseRoute(pathname: string): Route {
  if (pathname === "/") return { name: "library" };
  if (pathname === "/teach") return { name: "teach" };
  if (pathname === "/settings") return { name: "settings" };
  if (pathname.startsWith(CASE_PREFIX)) {
    const segment = pathname.slice(CASE_PREFIX.length);
    const caseId = segment.includes("/") ? null : decodeSegment(segment);
    if (caseId) return { name: "case", caseId };
  }
  return { name: "notFound" };
}

export function routePath(route: Route): string {
  switch (route.name) {
    case "library":
      return "/";
    case "case":
      return `${CASE_PREFIX}${encodeURIComponent(route.caseId)}`;
    case "teach":
      return "/teach";
    case "settings":
      return "/settings";
    case "notFound":
      return "/not-found";
  }
}

export function navigate(route: Route): void {
  window.history.pushState(null, "", routePath(route));
  // `pushState` fires no event; `useRoute` listens to `popstate`.
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
  };
}

export function useRoute(): Route {
  // The snapshot is the pathname string so it is stable between reads; the route is derived.
  const pathname = useSyncExternalStore(subscribe, () => window.location.pathname);
  return useMemo(() => parseRoute(pathname), [pathname]);
}

/** Written with `createElement` so the router stays a `.ts` file with no component exports. */
export function Link(props: { to: Route; className?: string; children: ReactNode }) {
  const { to, className, children } = props;
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    const modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
    if (event.defaultPrevented || event.button !== 0 || modified) return;
    event.preventDefault();
    navigate(to);
  };
  return createElement("a", { href: routePath(to), className, onClick }, children);
}
