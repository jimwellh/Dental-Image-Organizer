import { en } from "../strings/en";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export function ConfirmDialog(props: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { title, body, confirmLabel, onConfirm, onCancel } = props;
  return (
    <Dialog title={title} onClose={onCancel}>
      <p className="mb-5 text-sm text-slate-700">{body}</p>
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>{en.common.cancel}</Button>
        <Button variant="danger" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}
