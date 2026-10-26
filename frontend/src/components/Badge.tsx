import type { ReactNode } from "react";

type Tone = "neutral" | "ok" | "warn" | "error";

const TONES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-800",
  ok: "bg-green-100 text-green-900",
  warn: "bg-amber-100 text-amber-900",
  error: "bg-red-100 text-red-900",
};

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}
