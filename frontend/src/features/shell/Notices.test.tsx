import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { en } from "../../strings/en";
import { INFO_NOTICE_MS, useNotify } from "./notify";
import { NoticeProvider } from "./Notices";

function Trigger({ kind }: { kind: "info" | "error" }) {
  const notify = useNotify();
  return (
    <button
      type="button"
      onClick={() => {
        notify({ kind, lines: ["first", "second"] });
      }}
    >
      go
    </button>
  );
}

test("an info notice clears by itself", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(
    <NoticeProvider>
      <Trigger kind="info" />
    </NoticeProvider>,
  );
  await user.click(screen.getByRole("button", { name: "go" }));
  expect(screen.getByRole("status")).toHaveTextContent("first");
  expect(screen.getByRole("status")).toHaveTextContent("second");

  await act(() => vi.advanceTimersByTimeAsync(INFO_NOTICE_MS));
  expect(screen.queryByText("first")).not.toBeInTheDocument();
});

test("an error notice stays until dismissed", async () => {
  const user = userEvent.setup();
  render(
    <NoticeProvider>
      <Trigger kind="error" />
    </NoticeProvider>,
  );
  await user.click(screen.getByRole("button", { name: "go" }));
  expect(screen.getByRole("alert")).toHaveTextContent("first");

  await user.click(screen.getByRole("button", { name: en.common.dismiss }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
