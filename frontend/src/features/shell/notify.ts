import { createContext, useContext } from "react";

export const INFO_NOTICE_MS = 8000;

export interface Notice {
  id: number;
  kind: "info" | "error";
  lines: string[];
}

export type Notify = (notice: Omit<Notice, "id">) => void;

export const NoticeContext = createContext<Notify | null>(null);

export function useNotify(): Notify {
  const notify = useContext(NoticeContext);
  if (!notify) throw new Error("useNotify needs a NoticeProvider above it.");
  return notify;
}
