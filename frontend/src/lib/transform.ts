/**
 * 3 x 3 projective transforms, row-major, mapping photo points to output points. Both axes are
 * divided by the image's width (spec §4.8), so one matrix serves previews and full renders.
 *
 * This mirrors `core.transform`; `tests/fixtures/transform/cases.json` pins the two together.
 */

export type Mat3 = readonly [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
];
export type Point = readonly [number, number];

// Below this, a pivot or a point spread is rounding noise, not geometry. Landmarks are
// normalized to ~1, so 1e-9 sits far below any real click and far above float error.
const SINGULAR = 1e-9;

export function identity(): Mat3 {
  return [1, 0, 0, 0, 1, 0, 0, 0, 1];
}

export function fromArray(values: readonly number[]): Mat3 {
  if (values.length !== 9 || !values.every(Number.isFinite)) {
    throw new RangeError("A transform needs exactly nine finite numbers.");
  }
  const [a, b, c, d, e, f, g, h, i] = values as readonly [
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
    number,
  ];
  return [a, b, c, d, e, f, g, h, i];
}

export function multiply(a: Mat3, b: Mat3): Mat3 {
  const [a0, a1, a2, a3, a4, a5, a6, a7, a8] = a;
  const [b0, b1, b2, b3, b4, b5, b6, b7, b8] = b;
  return [
    a0 * b0 + a1 * b3 + a2 * b6,
    a0 * b1 + a1 * b4 + a2 * b7,
    a0 * b2 + a1 * b5 + a2 * b8,
    a3 * b0 + a4 * b3 + a5 * b6,
    a3 * b1 + a4 * b4 + a5 * b7,
    a3 * b2 + a4 * b5 + a5 * b8,
    a6 * b0 + a7 * b3 + a8 * b6,
    a6 * b1 + a7 * b4 + a8 * b7,
    a6 * b2 + a7 * b5 + a8 * b8,
  ];
}

export function normalize(h: Mat3): Mat3 {
  const scale = h[8];
  if (scale === 0) throw new RangeError("Cannot normalize a transform whose h[8] is 0.");
  return [
    h[0] / scale,
    h[1] / scale,
    h[2] / scale,
    h[3] / scale,
    h[4] / scale,
    h[5] / scale,
    h[6] / scale,
    h[7] / scale,
    1,
  ];
}

export function invert(h: Mat3): Mat3 {
  const [a, b, c, d, e, f, g, i, j] = h;
  const cofactors: Mat3 = [
    e * j - f * i,
    c * i - b * j,
    b * f - c * e,
    f * g - d * j,
    a * j - c * g,
    c * d - a * f,
    d * i - e * g,
    b * g - a * i,
    a * e - b * d,
  ];
  const det = a * cofactors[0] + b * cofactors[3] + c * cofactors[6];
  if (det === 0) throw new RangeError("Cannot invert a singular transform.");
  return normalize(cofactors.map((v) => v / det) as unknown as Mat3);
}

export function mapPoint(h: Mat3, p: Point): Point {
  const [x, y] = p;
  const w = h[6] * x + h[7] * y + h[8];
  return [(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w];
}

export function translation(tx: number, ty: number): Mat3 {
  return [1, 0, tx, 0, 1, ty, 0, 0, 1];
}

export function scaling(s: number): Mat3 {
  return [s, 0, 0, 0, s, 0, 0, 0, 1];
}

/** x' = scale * R(angle) * F * x + t, where F mirrors the x axis when `mirror` is set. */
export function similarity(
  scale: number,
  angleDeg: number,
  tx: number,
  ty: number,
  mirror = false,
): Mat3 {
  const angle = (angleDeg * Math.PI) / 180;
  const c = scale * Math.cos(angle);
  const s = scale * Math.sin(angle);
  // Mirroring on the right negates the first column.
  return mirror ? [-c, -s, tx, -s, c, ty, 0, 0, 1] : [c, -s, tx, s, c, ty, 0, 0, 1];
}

/** Moves the photo in output space. */
export function translateBy(h: Mat3, dx: number, dy: number): Mat3 {
  return multiply(translation(dx, dy), h);
}

function about(pivot: Point, op: Mat3): Mat3 {
  return multiply(multiply(translation(pivot[0], pivot[1]), op), translation(-pivot[0], -pivot[1]));
}

/** Scales the photo about a point of the output, which stays where it is. */
export function scaleAbout(h: Mat3, factor: number, pivot: Point): Mat3 {
  return multiply(about(pivot, scaling(factor)), h);
}

/** Rotates the photo about a point of the output; positive is clockwise on screen (y points down). */
export function rotateAbout(h: Mat3, deg: number, pivot: Point): Mat3 {
  return multiply(about(pivot, similarity(1, deg, 0, 0)), h);
}

function pairUp(src: readonly Point[], dst: readonly Point[]): Array<readonly [Point, Point]> {
  if (src.length !== dst.length) {
    throw new RangeError("Source and destination need the same number of points.");
  }
  return src.map((s, k) => [s, dst[k] as Point] as const);
}

// Umeyama's least-squares similarity in 2D: the best proper rotation has a closed form, so no
// SVD is needed.
function umeyama(pairs: ReadonlyArray<readonly [Point, Point]>): Mat3 {
  const n = pairs.length;
  const mean = (pick: (pair: readonly [Point, Point]) => Point): Point => [
    pairs.reduce((sum, pair) => sum + pick(pair)[0], 0) / n,
    pairs.reduce((sum, pair) => sum + pick(pair)[1], 0) / n,
  ];
  const muS = mean((pair) => pair[0]);
  const muD = mean((pair) => pair[1]);
  let dot = 0;
  let cross = 0;
  let variance = 0;
  for (const [s, d] of pairs) {
    const sx = s[0] - muS[0];
    const sy = s[1] - muS[1];
    const dx = d[0] - muD[0];
    const dy = d[1] - muD[1];
    dot += sx * dx + sy * dy;
    cross += sx * dy - sy * dx;
    variance += sx * sx + sy * sy;
  }
  if (variance < SINGULAR) {
    throw new RangeError("The source points coincide, so no transform can be fitted.");
  }
  const theta = Math.atan2(cross, dot);
  const scale = Math.hypot(dot, cross) / variance;
  const c = scale * Math.cos(theta);
  const s = scale * Math.sin(theta);
  return [
    c,
    -s,
    muD[0] - (c * muS[0] - s * muS[1]),
    s,
    c,
    muD[1] - (s * muS[0] + c * muS[1]),
    0,
    0,
    1,
  ];
}

/** Least-squares similarity from src onto dst; a mirror is used only if it fits better. */
export function fitSimilarity(src: readonly Point[], dst: readonly Point[]): Mat3 {
  if (src.length < 2) throw new RangeError("Fitting a similarity needs at least two point pairs.");
  const pairs = pairUp(src, dst);
  const mirroredPairs = pairs.map(([s, d]) => [[-s[0], s[1]] as Point, d] as const);
  const error = (h: Mat3) =>
    pairs.reduce((sum, [s, d]) => {
      const [x, y] = mapPoint(h, s);
      return sum + (x - d[0]) ** 2 + (y - d[1]) ** 2;
    }, 0);

  const plain = umeyama(pairs);
  const mirrored = multiply(umeyama(mirroredPairs), [-1, 0, 0, 0, 1, 0, 0, 0, 1]);
  // The margin keeps a mirror from winning on rounding noise alone.
  return error(mirrored) < error(plain) - 1e-12 ? mirrored : plain;
}

/** Exact homography through four point pairs, solved by Gaussian elimination; h[8] is 1. */
export function homographyFromPoints(src: readonly Point[], dst: readonly Point[]): Mat3 {
  if (src.length !== 4 || dst.length !== 4) {
    throw new RangeError("A homography needs exactly four point pairs.");
  }
  // Each row holds the 8 coefficients followed by the right-hand side.
  const rows: number[][] = [];
  for (const [[x, y], [u, v]] of pairUp(src, dst)) {
    rows.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    rows.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  const at = (r: number, c: number): number => (rows[r] as number[])[c] as number;
  for (let col = 0; col < 8; col++) {
    let pivot = col;
    for (let r = col + 1; r < 8; r++) {
      if (Math.abs(at(r, col)) > Math.abs(at(pivot, col))) pivot = r;
    }
    if (Math.abs(at(pivot, col)) < SINGULAR) {
      throw new RangeError("The four points are degenerate (three lie on a line).");
    }
    [rows[col], rows[pivot]] = [rows[pivot] as number[], rows[col] as number[]];
    for (let r = col + 1; r < 8; r++) {
      const factor = at(r, col) / at(col, col);
      const row = rows[r] as number[];
      for (let c = col; c < 9; c++) row[c] = at(r, c) - factor * at(col, c);
    }
  }
  const h: number[] = new Array<number>(8).fill(0);
  for (let r = 7; r >= 0; r--) {
    let sum = at(r, 8);
    for (let c = r + 1; c < 8; c++) sum -= at(r, c) * (h[c] as number);
    h[r] = sum / at(r, r);
  }
  return fromArray([...h, 1]);
}

/**
 * The similarity that best reproduces where `h` sends the output frame's corners; used by
 * "Reset perspective". The fit is done on the photo points that `h` maps onto the corners.
 */
export function closestSimilarity(h: Mat3, frameWidth: number, frameHeight: number): Mat3 {
  const corners: Point[] = [
    [0, 0],
    [frameWidth, 0],
    [frameWidth, frameHeight],
    [0, frameHeight],
  ];
  const inverse = invert(h);
  return fitSimilarity(
    corners.map((corner) => mapPoint(inverse, corner)),
    corners,
  );
}

/** Converts a normalized photo-to-output matrix to pixel units for the given image widths. */
export function toPixel(h: Mat3, inWidth: number, outWidth: number): Mat3 {
  return multiply(multiply(scaling(outWidth), h), scaling(1 / inWidth));
}

/** The CSS `matrix3d(...)` of the pixel matrix, for an element with `transform-origin: 0 0`. */
export function cssMatrix3d(h: Mat3, inWidth: number, outWidth: number): string {
  const p = toPixel(h, inWidth, outWidth);
  // CSS lists the 4 x 4 column by column; the 2D homography sits in rows and columns 1, 2, 4.
  // String(), not toFixed(): perspective terms are ~1e-5 per pixel and would round to 0.
  const cells = [p[0], p[3], 0, p[6], p[1], p[4], 0, p[7], 0, 0, 1, 0, p[2], p[5], 0, p[8]];
  return `matrix3d(${cells.map(String).join(", ")})`;
}

/** True when the last row is exactly [0, 0, 1], that is, the matrix has no perspective. */
export function isAffine(h: Mat3): boolean {
  return h[6] === 0 && h[7] === 0 && h[8] === 1;
}
