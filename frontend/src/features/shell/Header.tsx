import { Button } from "../../components/Button";
import { Link, useRoute, type Route } from "../../lib/router";
import { en } from "../../strings/en";

const LIBRARY: Route = { name: "library" };
const SETTINGS: Route = { name: "settings" };

export function Header({ onQuit }: { onQuit: () => void }) {
  const route = useRoute();
  const linkClass = (active: boolean) =>
    `rounded px-2 py-1 text-sm font-medium ${active ? "bg-slate-200 text-slate-900" : "text-slate-700 hover:bg-slate-100"}`;
  return (
    <header className="flex items-center gap-4 border-b border-slate-300 bg-white px-4 py-2">
      <Link to={LIBRARY} className="rounded text-base font-semibold text-slate-900">
        {en.app.name}
      </Link>
      <nav className="flex flex-1 items-center gap-1">
        <Link to={LIBRARY} className={linkClass(route.name === "library")}>
          {en.nav.library}
        </Link>
        <Link to={SETTINGS} className={linkClass(route.name === "settings")}>
          {en.nav.settings}
        </Link>
      </nav>
      <Button onClick={onQuit}>{en.nav.quit}</Button>
    </header>
  );
}
