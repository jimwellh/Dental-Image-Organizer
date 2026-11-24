// Formats a browser can decode but the server does not read. Apps other than Keynote may hand a
// copied or dragged image over as one of these; DNG and other raw types are not among them, so
// the server still explains why it refuses those.
const CONVERTIBLE: ReadonlySet<string> = new Set([
  "image/tiff",
  "image/webp",
  "image/bmp",
  "image/avif",
]);

export class UnreadableImageError extends Error {
  readonly fileName: string;

  constructor(fileName: string, options?: ErrorOptions) {
    super(`The browser could not decode ${fileName}.`, options);
    this.name = "UnreadableImageError";
    this.fileName = fileName;
  }
}

const pngName = (name: string): string => {
  const dot = name.lastIndexOf(".");
  return `${dot > 0 ? name.slice(0, dot) : name}.png`;
};

/**
 * The file as JPEG, PNG, or HEIC, which the server reads: other image types are re-encoded as a
 * lossless PNG. Throws `UnreadableImageError` when the browser cannot decode such a file.
 */
export async function toUploadable(file: File): Promise<File> {
  if (!CONVERTIBLE.has(file.type.toLowerCase())) return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch (cause) {
    throw new UnreadableImageError(file.name, { cause });
  }
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) throw new UnreadableImageError(file.name);
    context.drawImage(bitmap, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, "image/png");
    });
    if (!blob) throw new UnreadableImageError(file.name);
    // The upload time stands in for a capture time the image does not carry.
    return new File([blob], pngName(file.name), {
      type: "image/png",
      lastModified: file.lastModified,
    });
  } finally {
    // The decoded pixels can be hundreds of megabytes; they are not left to the collector.
    bitmap.close();
  }
}
