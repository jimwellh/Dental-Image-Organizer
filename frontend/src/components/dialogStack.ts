// Open dialogs, oldest first. Only the last one handles the keyboard, so a confirmation opened
// over a dialog does not also close or trap the dialog beneath it.
const openDialogs: object[] = [];

/** Registers an open dialog; the returned function removes it again. */
export function pushDialog(token: object): () => void {
  openDialogs.push(token);
  return () => {
    openDialogs.splice(openDialogs.indexOf(token), 1);
  };
}

export const isTopDialog = (token: object): boolean =>
  openDialogs[openDialogs.length - 1] === token;

/** Whether a dialog is open: page-wide shortcuts such as paste leave it alone then. */
export const anyDialogOpen = (): boolean => openDialogs.length > 0;
