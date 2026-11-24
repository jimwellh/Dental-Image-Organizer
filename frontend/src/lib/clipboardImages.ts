/**
 * The image files of a paste. A copy in Keynote, Preview, or Photos arrives as an image file:
 * Safari and Chrome on macOS both hand it over as a PNG named `image.png`. A copied document of
 * another kind is not a photo, so it is left out; a file with no type may still be HEIC.
 */
export function imagesFromClipboard(data: Pick<DataTransfer, "items" | "files"> | null): File[] {
  if (!data) return [];
  const fromItems = Array.from(data.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
  // Some browsers fill only one of the two lists.
  const files = fromItems.length > 0 ? fromItems : Array.from(data.files);
  return files.filter((file) => file.type === "" || file.type.startsWith("image/"));
}

// Inputs that take no typed text, so a paste while one has focus is meant for the page.
const NON_TEXT_INPUTS: ReadonlySet<string> = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

/** Whether a paste lands in a field that takes text, where it belongs to that field. */
export function pastedIntoText(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(target.type);
  return target.closest("textarea, [contenteditable]:not([contenteditable='false'])") !== null;
}
