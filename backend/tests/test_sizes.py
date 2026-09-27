"""サイズ検証(境界値)。"""

from __future__ import annotations

import pytest

from app.domain.sizes import InvalidSizeError, parse_size, validate_size


def test_valid_size_1024_square() -> None:
    validate_size(1024, 1024)  # 例外が出なければOK


def test_valid_size_min_total_pixels() -> None:
    # 640x1024 = 655,360 (下限ちょうど)、16の倍数、アスペクト比1.6
    validate_size(640, 1024)


def test_valid_size_max_total_pixels() -> None:
    validate_size(3840, 2160)  # 8,294,400 (上限ちょうど)


def test_invalid_not_multiple_of_16() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(1000, 1000)


def test_invalid_long_edge_too_large() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(3856, 2160)


def test_invalid_total_pixels_too_small() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(512, 512)  # 262,144 < 655,360


def test_invalid_total_pixels_too_large() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(3840, 2176)  # 8,355,840 > 8,294,400


def test_invalid_aspect_ratio_too_wide() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(3840, 1008)  # 比率 3.8... > 3


def test_invalid_aspect_ratio_too_tall() -> None:
    with pytest.raises(InvalidSizeError):
        validate_size(1008, 3840)


def test_parse_size_auto_returns_none() -> None:
    assert parse_size("auto") is None


def test_parse_size_parses_width_height() -> None:
    assert parse_size("1536x1024") == (1536, 1024)


def test_parse_size_rejects_malformed_string() -> None:
    with pytest.raises(InvalidSizeError):
        parse_size("not-a-size")


def test_parse_size_validates_constraints() -> None:
    with pytest.raises(InvalidSizeError):
        parse_size("100x100")
