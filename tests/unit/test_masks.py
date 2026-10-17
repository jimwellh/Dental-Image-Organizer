import numpy as np

from dental_image_organizer.core.masks import gingiva_mask, tooth_mask
from support.synthetic import make_scene


def test_tooth_mask_finds_teeth_and_not_gum() -> None:
    scene = make_scene(1, 1200, 800)
    teeth = tooth_mask(scene)

    share = teeth.mean()
    assert 0.10 < share < 0.30
    assert teeth[int(800 * 0.40), int(1200 * 0.08)]  # center of the first upper tooth
    assert not teeth[20, 600]  # gum near the top edge


def test_gingiva_mask_excludes_teeth() -> None:
    scene = make_scene(2, 1200, 800)
    teeth = tooth_mask(scene)

    gums = gingiva_mask(scene, teeth)

    assert gums.mean() > 0.4
    assert not np.any(gums & teeth)


def test_gingiva_mask_never_overlaps_the_given_tooth_mask() -> None:
    gum = make_scene(4, 600, 400, teeth=False)
    teeth = np.zeros((400, 600), bool)
    teeth[:, 300:303] = True  # a gap narrower than the cleanup kernel

    gums = gingiva_mask(gum, teeth)

    assert not np.any(gums & teeth)


def test_scene_without_teeth_gives_an_empty_tooth_mask() -> None:
    scene = make_scene(3, 1200, 800, teeth=False)

    assert tooth_mask(scene).mean() < 0.01
