import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { useId, type KeyboardEvent, type ReactNode } from "react";
import type { Slot } from "../api/types";
import { SLOTS } from "../lib/slots";
import { fill } from "../lib/text";
import { en } from "../strings/en";
import { Badge } from "./Badge";

export interface SlotBoardProps<T> {
  cells: Readonly<Record<Slot, T | null>>;
  tray: readonly T[];
  idOf: (item: T) => string;
  /** For the label of the "Move to" select. */
  nameOf: (item: T) => string;
  canMove: (item: T) => boolean;
  renderItem: (item: T, where: Slot | "tray") => ReactNode;
  /** Low confidence: the item gets a ring and a text, never a colour alone. */
  highlight?: (item: T) => boolean;
  onMove: (id: string, target: Slot | "tray") => void;
  /** Enter or a double click on a placed item. */
  onOpen?: (item: T) => void;
}

type Where = Slot | "tray";

const isWhere = (value: string): value is Where =>
  value === "tray" || SLOTS.some((slot) => slot === value);

// The pointer decides when it is over a target; the dragged box alone would pick the neighbour of
// a small cell. A keyboard drag has no pointer position, so it falls back to overlap.
const collision: CollisionDetection = (args) => {
  const underPointer = pointerWithin(args);
  return underPointer.length > 0 ? underPointer : rectIntersection(args);
};

function Item<T>(props: { item: T; where: Where; board: SlotBoardProps<T> }) {
  const { item, where, board } = props;
  const { idOf, nameOf, canMove, renderItem, highlight, onMove, onOpen } = board;
  const itemId = idOf(item);
  const name = nameOf(item);
  const movable = canMove(item);
  const flagged = highlight?.(item) ?? false;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, isDragging } =
    useDraggable({ id: itemId, disabled: !movable });
  const open =
    where !== "tray" && onOpen
      ? () => {
          onOpen(item);
        }
      : undefined;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // Enter on the handle or the select belongs to them.
    if (event.key === "Enter" && event.target === event.currentTarget) open?.();
  };

  return (
    <div
      ref={setNodeRef}
      role="group"
      aria-label={name}
      tabIndex={open ? 0 : undefined}
      onDoubleClick={open}
      onKeyDown={open ? onKeyDown : undefined}
      style={{ transform: CSS.Translate.toString(transform) }}
      className={`relative flex flex-col gap-1 rounded-md bg-white p-1 ${
        flagged ? "ring-2 ring-amber-500" : "ring-1 ring-slate-300"
      } ${isDragging ? "z-20 opacity-80 shadow-lg" : ""}`}
    >
      {movable && (
        // The handle carries the drag so the select inside the item stays a plain control; the
        // `attributes` give it a name for assistive technology and Space/Enter to lift it.
        <span
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
          aria-label={fill(en.board.drag, { name })}
          title={fill(en.board.drag, { name })}
          className="absolute right-1 top-1 z-10 cursor-grab touch-none select-none rounded bg-white/90 px-1 text-slate-700"
        >
          <span aria-hidden="true">⠿</span>
        </span>
      )}
      {renderItem(item, where)}
      {flagged && <Badge tone="warn">{en.board.checkPlacement}</Badge>}
      <select
        aria-label={fill(en.board.moveTo, { name })}
        value={where}
        disabled={!movable}
        onChange={(event) => {
          const target = event.target.value;
          if (isWhere(target) && target !== where) onMove(itemId, target);
        }}
        className="rounded-md border border-slate-400 bg-white px-1 py-0.5 text-xs text-slate-900 disabled:opacity-50"
      >
        {SLOTS.map((slot) => (
          <option key={slot} value={slot}>
            {en.slots[slot]}
          </option>
        ))}
        <option value="tray">{en.board.tray}</option>
      </select>
    </div>
  );
}

function Cell<T>(props: { slot: Slot; item: T | null; board: SlotBoardProps<T> }) {
  const { slot, item, board } = props;
  const labelId = useId();
  const { setNodeRef, isOver } = useDroppable({ id: slot });
  return (
    <div
      ref={setNodeRef}
      role="group"
      aria-labelledby={labelId}
      className={`flex min-h-32 flex-col gap-1 rounded-lg border p-1 ${
        isOver ? "border-sky-600 bg-sky-50" : "border-slate-300 bg-slate-50"
      }`}
    >
      <span id={labelId} className="text-xs font-medium text-slate-800">
        {en.slots[slot]}
      </span>
      {item === null ? (
        <span className="m-auto text-xs text-slate-600">{en.board.empty}</span>
      ) : (
        <Item item={item} where={slot} board={board} />
      )}
    </div>
  );
}

function Tray<T>(props: { items: readonly T[]; board: SlotBoardProps<T> }) {
  const { items, board } = props;
  const labelId = useId();
  const { setNodeRef, isOver } = useDroppable({ id: "tray" });
  return (
    <div
      ref={setNodeRef}
      role="group"
      aria-labelledby={labelId}
      className={`flex flex-col gap-2 rounded-lg border p-2 ${
        isOver ? "border-sky-600 bg-sky-50" : "border-slate-300 bg-slate-50"
      }`}
    >
      <span id={labelId} className="text-sm font-medium text-slate-900">
        {en.board.tray}
      </span>
      {items.length === 0 ? (
        <p className="text-xs text-slate-600">{en.board.trayEmpty}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-2">
          {items.map((item) => (
            <Item key={board.idOf(item)} item={item} where="tray" board={board} />
          ))}
        </div>
      )}
    </div>
  );
}

/** The 3 x 3 slots and the tray below them; items move by drag or by their "Move to" select. */
export function SlotBoard<T>(props: SlotBoardProps<T>) {
  const { cells, tray, idOf, onMove } = props;
  const sensors = useSensors(
    // A small distance keeps a click on the handle from starting a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor),
  );

  const whereOf = (id: string): Where | null => {
    for (const slot of SLOTS) {
      const item = cells[slot];
      if (item !== null && idOf(item) === id) return slot;
    }
    return tray.some((item) => idOf(item) === id) ? "tray" : null;
  };

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over) return;
    const id = String(active.id);
    const target = String(over.id);
    if (isWhere(target) && target !== whereOf(id)) onMove(id, target);
  };

  return (
    <DndContext sensors={sensors} collisionDetection={collision} onDragEnd={onDragEnd}>
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-3 gap-2">
          {SLOTS.map((slot) => (
            <Cell key={slot} slot={slot} item={cells[slot]} board={props} />
          ))}
        </div>
        <Tray items={tray} board={props} />
      </div>
    </DndContext>
  );
}
