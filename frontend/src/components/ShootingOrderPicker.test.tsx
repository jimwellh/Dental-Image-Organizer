import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Slot } from "../api/types";
import { SLOTS } from "../lib/slots";
import { fill } from "../lib/text";
import { en } from "../strings/en";
import { ShootingOrderPicker } from "./ShootingOrderPicker";

// A cell's name starts with its view; a chosen one adds its position after it.
const cell = (slot: Slot) => screen.getByRole("button", { name: new RegExp(`^${en.slots[slot]}`) });

describe("ShootingOrderPicker", () => {
  it("shows each view's position in the current order", () => {
    const reversed = [...SLOTS].reverse();
    render(<ShootingOrderPicker value={reversed} onChange={vi.fn()} />);

    expect(cell("upper_right")).toHaveAccessibleName(
      fill(en.teach.order.position, { name: en.slots.upper_right, n: "9" }),
    );
    expect(cell("lower_left")).toHaveAccessibleName(
      fill(en.teach.order.position, { name: en.slots.lower_left, n: "1" }),
    );
  });

  it("clicking all nine views saves the order", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ShootingOrderPicker value={SLOTS} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: en.teach.order.change }));
    expect(screen.getByText(en.teach.order.instruction)).toBeVisible();
    const reversed = [...SLOTS].reverse();
    for (const slot of reversed) await user.click(cell(slot));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(reversed);
    // Picking is over: the instruction is gone and the button is back.
    expect(screen.queryByText(en.teach.order.instruction)).toBeNull();
    expect(screen.getByRole("button", { name: en.teach.order.change })).toBeVisible();
  });

  it("ignores a view clicked twice", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ShootingOrderPicker value={SLOTS} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: en.teach.order.change }));
    await user.click(cell("lower_left"));
    await user.click(cell("lower_left"));
    for (const slot of SLOTS.filter((s) => s !== "lower_left")) await user.click(cell(slot));

    expect(onChange).toHaveBeenCalledWith([
      "lower_left",
      ...SLOTS.filter((s) => s !== "lower_left"),
    ]);
  });

  it("cancel keeps the order", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<ShootingOrderPicker value={SLOTS} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: en.teach.order.change }));
    for (const slot of SLOTS.slice(0, 3)) await user.click(cell(slot));
    await user.click(screen.getByRole("button", { name: en.common.cancel }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: en.teach.order.change })).toBeVisible();
  });

  it("cannot be changed while disabled", () => {
    render(<ShootingOrderPicker value={SLOTS} onChange={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: en.teach.order.change })).toBeDisabled();
  });
});
