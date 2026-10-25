import type { Flip, ImageSize, Slot } from "./types";

test("the generated schema names the slots, flips, and image sizes", () => {
  expectTypeOf<Slot>().toEqualTypeOf<
    | "upper_right"
    | "upper_occlusal"
    | "upper_left"
    | "occlusion_right"
    | "occlusion_front"
    | "occlusion_left"
    | "lower_right"
    | "lower_occlusal"
    | "lower_left"
  >();
  expectTypeOf<Flip>().toEqualTypeOf<"none" | "horizontal" | "vertical" | "rotate_180">();
  expectTypeOf<ImageSize>().toEqualTypeOf<"thumb" | "preview" | "full">();
});
