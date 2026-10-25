import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createQueryClient } from "./api/queries";
import { describeError } from "./api/http";
import { Link, useRoute, type Route } from "./lib/router";
import { LibraryScreen } from "./features/library/LibraryScreen";
import { Shell } from "./features/shell/Shell";
import { useNotify } from "./features/shell/notify";
import { NoticeProvider } from "./features/shell/Notices";
import { en } from "./strings/en";

const LIBRARY: Route = { name: "library" };

// Placeholders until the screens of Tasks 10-16 replace them.
function Screen({ route }: { route: Route }) {
  switch (route.name) {
    case "library":
      return <LibraryScreen />;
    case "case":
      return <h1 className="text-xl font-semibold">{en.nav.case}</h1>;
    case "teach":
      return <h1 className="text-xl font-semibold">{en.nav.teach}</h1>;
    case "settings":
      return <h1 className="text-xl font-semibold">{en.nav.settings}</h1>;
    case "notFound":
      return (
        <div>
          <p className="mb-2">{en.app.notFound}</p>
          <Link to={LIBRARY} className="text-sky-800 underline">
            {en.app.backToLibrary}
          </Link>
        </div>
      );
  }
}

function Routed() {
  const notify = useNotify();
  // Created once: the client owns the cache, and `notify` is stable.
  const [client] = useState(() =>
    createQueryClient((error) => {
      notify({ kind: "error", lines: [describeError(error)] });
    }),
  );
  const route = useRoute();
  return (
    <QueryClientProvider client={client}>
      <Shell>
        <Screen route={route} />
      </Shell>
    </QueryClientProvider>
  );
}

export function App() {
  return (
    <NoticeProvider>
      <Routed />
    </NoticeProvider>
  );
}
