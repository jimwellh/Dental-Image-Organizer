from datetime import date

import numpy as np
import pytest

from dental_image_organizer.core.layout import render_composite
from dental_image_organizer.core.naming import composite_capture_date
from dental_image_organizer.core.slots import SLOTS


def test_composite_places_cells_row_major_with_gaps() -> None:
    cells = {slot: np.full((20, 30, 3), i * 20, np.uint8) for i, slot in enumerate(SLOTS)}

    grid = render_composite(cells, (30, 20), gap_px=2, gap_color=(255, 0, 0))

    assert grid.shape == (64, 94, 3)
    assert grid[0, 0, 0] == 0 and grid[0, 32, 0] == 20 and grid[22, 0, 0] == 60
    assert tuple(grid[0, 30]) == (255, 0, 0)


def test_empty_slots_show_the_gap_color() -> None:
    grid = render_composite({"occlusion_front": np.full((20, 30, 3), 200, np.uint8)}, (30, 20))

    assert grid[30, 45, 0] == 200
    assert grid[:20].max() == 0


def test_wrong_cell_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_composite({"upper_right": np.zeros((10, 10, 3), np.uint8)}, (30, 20))


def test_composite_date_is_the_most_frequent_then_earliest() -> None:
    d1, d2 = date(2026, 10, 16), date(2026, 10, 17)

    assert composite_capture_date([d2, d1, d2]) == d2
    assert composite_capture_date([d2, d1]) == d1


def test_composite_date_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        composite_capture_date([])
