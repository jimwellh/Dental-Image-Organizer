import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Slot } from "../api/types";
import { fill } from "../lib/text";
import { en } from "../strings/en";
import { SLOTS } from "../lib/slots";
import { SlotBoard } from "./SlotBoard";

// Not a photo: the board must work for any item type, as the teaching screen uses it.
interface Card {
  key: string;
  label: string;
  locked?: boolean;
}

const empty = Object.fromEntries(SLOTS.map((slot) => [slot, null])) as Record<Slot, Card | null>;
const placed: Card = { key: "p", label: "Placed card" };
const loose: Card = { key: "t", label: "Loose card" };
const locked: Card = { key: "l", label: "Locked card", locked: true };

function renderBoard(overrides: Partial<Parameters<typeof SlotBoard<Card>>[0]> = {}) {
  const onMove = vi.fn();
  render(
    <SlotBoard<Card>
      cells={{ ...empty, upper_left: placed }}
      tray={[loose, locked]}
      idOf={(card) => card.key}
      nameOf={(card) => card.label}
      canMove={(card) => card.locked !== true}
      renderItem={(card) => <span>{`body of ${card.label}`}</span>}
      onMove={onMove}
      {...overrides}
    />,
  );
  return { onMove };
}

describe("SlotBoard", () => {
  it("lays the nine cells out in slot order, labelled", () => {
    renderBoard();
    const labels = SLOTS.map((slot) => screen.getByRole("group", { name: en.slots[slot] }));
    const all = screen.getAllByRole("group");
    const positions = labels.map((label) => all.indexOf(label));
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
    expect(
      within(screen.getByRole("group", { name: en.slots.upper_left })).getByText(
        "body of Placed card",
      ),
    ).toBeVisible();
  });

  it("the select moves an item to a slot or to the tray", async () => {
    const { onMove } = renderBoard();
    const user = userEvent.setup();

    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "Loose card" }) }),
      en.slots.lower_right,
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "Placed card" }) }),
      en.board.tray,
    );

    expect(onMove.mock.calls).toEqual([
      ["t", "lower_right"],
      ["p", "tray"],
    ]);
  });

  it("an item that cannot move has no way to be moved", () => {
    renderBoard();

    expect(
      screen.getByRole("combobox", { name: fill(en.board.moveTo, { name: "Locked card" }) }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: fill(en.board.drag, { name: "Locked card" }) }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: fill(en.board.drag, { name: "Loose card" }) }),
    ).toBeVisible();
  });

  it("flags an item for a second look with text, not colour alone", () => {
    renderBoard({ highlight: (card) => card.key === "p" });

    const cell = screen.getByRole("group", { name: en.slots.upper_left });
    expect(within(cell).getByText(en.board.checkPlacement)).toBeVisible();
    expect(screen.getAllByText(en.board.checkPlacement)).toHaveLength(1);
  });

  it("opens a placed item by double click or Enter, but not one in the tray", async () => {
    const onOpen = vi.fn();
    renderBoard({ onOpen });
    const user = userEvent.setup();

    await user.dblClick(screen.getByText("body of Placed card"));
    expect(onOpen).toHaveBeenCalledTimes(1);

    screen.getByRole("group", { name: "Placed card" }).focus();
    await user.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(onOpen).toHaveBeenLastCalledWith(placed);

    await user.dblClick(screen.getByText("body of Loose card"));
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
});
