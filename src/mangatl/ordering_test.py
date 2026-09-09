from __future__ import annotations

import pytest

from .models import BBox
from .ordering import reading_order


def box(x: int, y: int, w: int = 100, h: int = 50) -> BBox:
    return BBox(x=x, y=y, w=w, h=h)


def test_returns_empty_for_no_boxes():
    assert reading_order([]) == []


@pytest.mark.parametrize(
    ("name", "boxes", "expected"),
    [
        (
            "uma faixa ordena da esquerda para a direita",
            [box(400, 100), box(50, 100), box(220, 100)],
            [1, 2, 0],
        ),
        (
            "duas faixas separadas ordenam de cima para baixo",
            [box(50, 400), box(300, 100), box(50, 100)],
            [2, 1, 0],
        ),
        (
            "topos desalinhados dentro da tolerancia contam como mesma faixa",
            [box(300, 118), box(50, 100)],
            [1, 0],
        ),
        (
            "desalinhamento acima da tolerancia quebra em faixas",
            [box(300, 145), box(50, 100)],
            [1, 0],
        ),
        (
            "balao alto agrupa com balao baixo que ele cobre",
            [box(300, 110, h=30), box(50, 100, h=200)],
            [1, 0],
        ),
    ],
)
def test_orders_boxes(name: str, boxes: list[BBox], expected: list[int]):
    assert reading_order(boxes) == expected, name


def test_orders_right_to_left_when_rtl():
    boxes = [box(50, 100), box(400, 100), box(220, 100)]
    assert reading_order(boxes, rtl=True) == [1, 2, 0]


def test_keeps_vertical_order_across_bands_when_rtl():
    boxes = [box(50, 400), box(400, 100), box(50, 100)]
    assert reading_order(boxes, rtl=True) == [1, 2, 0]


def test_separates_bands_when_overlap_threshold_is_strict():
    boxes = [box(300, 118), box(50, 100)]
    assert reading_order(boxes, band_overlap=0.9) == [1, 0]
