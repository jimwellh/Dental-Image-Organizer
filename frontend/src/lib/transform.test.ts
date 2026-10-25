import fixture from "../../../tests/fixtures/transform/cases.json";
import {
  closestSimilarity,
  cssMatrix3d,
  fitSimilarity,
  fromArray,
  homographyFromPoints,
  invert,
  isAffine,
  mapPoint,
  multiply,
  rotateAbout,
  scaleAbout,
  similarity,
  translateBy,
  type Mat3,
  type Point,
} from "./transform";

function at(values: readonly number[], i: number): number {
  const v = values[i];
  if (v === undefined) throw new RangeError(`no value at index ${String(i)}`);
  return v;
}

function expectClose(actual: readonly number[], expected: readonly number[], tol: number) {
  expect(actual).toHaveLength(expected.length);
  expected.forEach((e, i) => {
    const a = at(actual, i);
    expect(Math.abs(a - e), `index ${String(i)}: ${String(a)} vs ${String(e)}`).toBeLessThanOrEqual(
      tol * Math.max(1, Math.abs(e)),
    );
  });
}

const points = (list: number[][]): Point[] => list.map(([x, y]) => [x as number, y as number]);
const flat = (list: readonly Point[]): number[] => list.flatMap((p) => [...p]);

test.each(fixture.multiply)("multiply matches core.transform", ({ a, b, expected }) => {
  expectClose(multiply(fromArray(a), fromArray(b)), expected, 1e-9);
});

test.each(fixture.invert)("invert matches core.transform", ({ h, expected }) => {
  expectClose(invert(fromArray(h)), expected, 1e-9);
});

test.each(fixture.map_points)("mapPoint matches map_points", ({ h, points: pts, expected }) => {
  const mapped = points(pts).map((p) => mapPoint(fromArray(h), p));
  expectClose(flat(mapped), expected.flat(), 1e-9);
});

test.each(fixture.similarity)("similarity matches core.transform", (c) => {
  expectClose(similarity(c.scale, c.angle_deg, c.tx, c.ty, c.mirror), c.expected, 1e-9);
});

test.each(fixture.fit_similarity)("fitSimilarity matches fit_similarity", (c) => {
  expectClose(fitSimilarity(points(c.src), points(c.dst)), c.expected, 1e-9);
});

test.each(fixture.homography_from_points)("homographyFromPoints matches the reference", (c) => {
  expectClose(homographyFromPoints(points(c.src), points(c.dst)), c.expected, 1e-9);
});

test.each(fixture.closest_similarity)("closestSimilarity matches core.transform", (c) => {
  const [w, h] = c.frame as [number, number];
  expectClose(closestSimilarity(fromArray(c.h), w, h), c.expected, 1e-9);
});

test.each(fixture.to_pixel)("matrix3d maps input pixels as to_pixel does", (c) => {
  const css = cssMatrix3d(fromArray(c.h), c.in_width, c.out_width);
  const body = /^matrix3d\((.*)\)$/.exec(css)?.[1];
  const m = (body ?? "").split(",").map(Number);
  expect(m).toHaveLength(16);
  const mapped = points(c.points).map(([x, y]): Point => {
    const w = at(m, 3) * x + at(m, 7) * y + at(m, 15);
    return [
      (at(m, 0) * x + at(m, 4) * y + at(m, 12)) / w,
      (at(m, 1) * x + at(m, 5) * y + at(m, 13)) / w,
    ];
  });
  expectClose(flat(mapped), c.expected_points.flat(), 1e-6);
});

// The fifth matrix of the fixture is a similarity with perspective terms.
const homography = (): Mat3 => fromArray(fixture.invert[4]?.h ?? []);
const sample = (): Mat3 => homography();

test("translateBy moves every mapped point", () => {
  const h = sample();
  const p: Point = [0.3, 0.4];
  const [x, y] = mapPoint(h, p);
  expectClose(mapPoint(translateBy(h, 0.1, -0.05), p), [x + 0.1, y - 0.05], 1e-12);
});

test("scaleAbout and rotateAbout keep the pivot fixed", () => {
  const h = sample();
  const pivot: Point = [0.4, 0.3];
  const photoPoint = mapPoint(invert(h), pivot);
  expectClose(mapPoint(scaleAbout(h, 1.25, pivot), photoPoint), pivot, 1e-12);
  expectClose(mapPoint(rotateAbout(h, 15, pivot), photoPoint), pivot, 1e-12);
});

test("scaleAbout scales and rotateAbout turns clockwise on screen", () => {
  const origin: Point = [0, 0];
  const id = similarity(1, 0, 0, 0);
  expectClose(mapPoint(scaleAbout(id, 2, origin), [1, 1]), [2, 2], 1e-12);
  // With y pointing down, +90 degrees takes the right-hand point to the bottom.
  expectClose(mapPoint(rotateAbout(id, 90, origin), [1, 0]), [0, 1], 1e-12);
});

test("fromArray refuses anything but nine finite numbers", () => {
  expect(() => fromArray([1, 0, 0, 0, 1, 0, 0, 0])).toThrow(RangeError);
  expect(() => fromArray([1, 0, 0, 0, 1, 0, 0, 0, Number.NaN])).toThrow(RangeError);
});

test("degenerate landmarks are refused", () => {
  expect(() =>
    fitSimilarity(
      [
        [0.5, 0.5],
        [0.5, 0.5],
      ],
      [
        [0.1, 0.1],
        [0.9, 0.9],
      ],
    ),
  ).toThrow(RangeError);
  const dst: Point[] = [
    [0, 0],
    [1, 0],
    [1, 1],
    [0, 1],
  ];
  const collinear: Point[] = [
    [0.1, 0.1],
    [0.5, 0.5],
    [0.9, 0.9],
    [0.2, 0.8],
  ];
  expect(() => homographyFromPoints(collinear, dst)).toThrow(RangeError);
});

test("isAffine", () => {
  expect(isAffine(similarity(1.3, 12, 0.05, -0.02))).toBe(true);
  expect(isAffine(homography())).toBe(false);
});
