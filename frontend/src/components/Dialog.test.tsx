import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { Dialog } from "./Dialog";

function Harness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        onClick={() => {
          setOpen(true);
        }}
      >
        Open
      </Button>
      {open && (
        <Dialog
          title="Title"
          onClose={() => {
            onClose();
            setOpen(false);
          }}
        >
          <Button>Inside</Button>
        </Dialog>
      )}
    </>
  );
}

test("Escape closes the dialog and focus returns", async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  render(<Harness onClose={onClose} />);
  const opener = screen.getByRole("button", { name: "Open" });
  await user.click(opener);

  const dialog = screen.getByRole("dialog", { name: "Title" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(screen.getByRole("button", { name: "Inside" })).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

test("Tab stays inside the dialog", async () => {
  const user = userEvent.setup();
  render(
    <>
      <Button>Outside</Button>
      <Dialog title="Title" onClose={vi.fn()}>
        <Button>One</Button>
        <Button>Two</Button>
      </Dialog>
    </>,
  );
  expect(screen.getByRole("button", { name: "One" })).toHaveFocus();
  await user.tab();
  expect(screen.getByRole("button", { name: "Two" })).toHaveFocus();
  await user.tab();
  expect(screen.getByRole("button", { name: "One" })).toHaveFocus();
  await user.tab({ shift: true });
  expect(screen.getByRole("button", { name: "Two" })).toHaveFocus();
});

test("ConfirmDialog confirms or cancels", async () => {
  const user = userEvent.setup();
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmDialog
      title="Delete?"
      body="This cannot be undone."
      confirmLabel="Delete"
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );
  expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Delete" }));
  expect(onConfirm).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(onCancel).toHaveBeenCalledOnce();
});
