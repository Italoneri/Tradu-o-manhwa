from __future__ import annotations

import pytest

from .models import BBox
from .pipeline import _drop_repeated_readings


def reading(x: int, y: int, w: int, h: int, text: str, confidence: float = 90.0):
    return (BBox(x=x, y=y, w=w, h=h), text, confidence)


def texts(readings) -> list[str]:
    return [text for _, text, _ in readings]


def boxes(readings) -> list[BBox]:
    return [box for box, _, _ in readings]


def test_keeps_a_single_reading_untouched():
    entrada = [reading(0, 0, 100, 50, "OLA")]

    kept, removed = _drop_repeated_readings(entrada)

    assert kept == entrada
    assert removed == []


def test_drops_the_same_text_read_from_an_overlapping_box():
    painel = reading(0, 1236, 998, 230, "LORD OF THE ZHUGE CLAN.")
    balao = reading(570, 1036, 360, 417, "LORD OF THE ZHUGE CLAN.")

    kept, removed = _drop_repeated_readings([painel, balao])

    assert texts(kept) == ["LORD OF THE ZHUGE CLAN."]
    assert len(removed) == 1


def test_keeps_the_tighter_box_of_the_pair():
    """A caixa menor e a do balao; a maior e o painel que o contem."""
    painel = reading(0, 1236, 998, 230, "FALA")
    balao = reading(570, 1236, 360, 200, "FALA")

    kept, _ = _drop_repeated_readings([painel, balao])

    assert boxes(kept) == [BBox(x=570, y=1236, w=360, h=200)]


def test_keeps_the_tighter_box_whichever_comes_first():
    painel = reading(0, 1236, 998, 230, "FALA")
    balao = reading(570, 1236, 360, 200, "FALA")

    a, _ = _drop_repeated_readings([painel, balao])
    b, _ = _drop_repeated_readings([balao, painel])

    assert boxes(a) == boxes(b)


def test_keeps_identical_text_in_boxes_that_do_not_touch():
    """Duas falas iguais em baloes distintos sao repeticao legitima da HQ."""
    entrada = [reading(0, 0, 100, 50, "..."), reading(500, 700, 100, 50, "...")]

    kept, removed = _drop_repeated_readings(entrada)

    assert len(kept) == 2
    assert removed == []


def test_keeps_different_texts_in_overlapping_boxes():
    entrada = [reading(0, 0, 400, 300, "PRIMEIRA"), reading(100, 100, 200, 150, "SEGUNDA")]

    kept, removed = _drop_repeated_readings(entrada)

    assert texts(kept) == ["PRIMEIRA", "SEGUNDA"]
    assert removed == []


def test_collapses_three_readings_of_one_bubble():
    entrada = [
        reading(0, 0, 900, 400, "FALA"),
        reading(100, 50, 400, 300, "FALA"),
        reading(150, 80, 200, 120, "FALA"),
    ]

    kept, removed = _drop_repeated_readings(entrada)

    assert len(kept) == 1
    assert boxes(kept) == [BBox(x=150, y=80, w=200, h=120)]
    assert len(removed) == 2


@pytest.mark.parametrize("entrada", [[], [reading(0, 0, 10, 10, "")]])
def test_survives_degenerate_input(entrada):
    kept, removed = _drop_repeated_readings(entrada)

    assert len(kept) == len(entrada)
    assert removed == []
