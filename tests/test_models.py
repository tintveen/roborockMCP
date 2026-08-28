from __future__ import annotations

import pytest
from pydantic import ValidationError

from roborock_mcp.models import BoundaryGeometry, CleaningSettings, Motion, Point, Zone


def test_zone_requires_ordered_coordinates() -> None:
    with pytest.raises(ValidationError):
        Zone(min_x_mm=10, min_y_mm=0, max_x_mm=1, max_y_mm=10)


def test_boundary_requires_exactly_one_geometry() -> None:
    with pytest.raises(ValidationError):
        BoundaryGeometry()
    geometry = BoundaryGeometry(rectangle=Zone(min_x_mm=0, min_y_mm=0, max_x_mm=10, max_y_mm=10))
    assert geometry.rectangle is not None


def test_settings_require_a_value() -> None:
    with pytest.raises(ValidationError):
        CleaningSettings()


def test_motion_bounds() -> None:
    assert Motion(direction="forward", duration_ms=100).speed == "slow"
    with pytest.raises(ValidationError):
        Motion(direction="forward", duration_ms=10)


def test_point_bounds() -> None:
    assert Point(x_mm=0, y_mm=100_000).y_mm == 100_000
    with pytest.raises(ValidationError):
        Point(x_mm=-1, y_mm=0)
