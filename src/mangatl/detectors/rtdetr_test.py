"""Funcoes puras do backend rtdetr, sem carregar o modelo.

Os numeros de score e as caixas vieram de paginas reais do capitulo manhwa/001,
para o teste calibrar nos valores que o modelo devolve de verdade.
"""

from __future__ import annotations

import numpy as np
import pytest

from ..models import BBox
from .rtdetr import (
    RawDetection,
    clamped_bbox,
    overlap_ratio,
    pair_detections,
    split_strips,
    stitch_detections,
)

ARTWORK = 0.60


def raw(label: str, score: float, x: int, y: int, w: int, h: int) -> RawDetection:
    return RawDetection(label=label, score=score, bbox=BBox(x=x, y=y, w=w, h=h))


@pytest.mark.parametrize(
    ("name", "corners", "expected"),
    [
        ("mantem a caixa que cabe na pagina", (10.0, 20.0, 110.0, 220.0), BBox(x=10, y=20, w=100, h=200)),
        ("prende o topo negativo em zero", (407.0, -1.0, 689.0, 100.0), BBox(x=407, y=0, w=282, h=100)),
        ("prende a borda direita na largura", (900.0, 10.0, 1200.0, 60.0), BBox(x=900, y=10, w=98, h=50)),
        ("aceita cantos invertidos", (110.0, 220.0, 10.0, 20.0), BBox(x=10, y=20, w=100, h=200)),
        ("descarta caixa inteiramente fora", (1100.0, 10.0, 1300.0, 60.0), None),
        ("descarta caixa sem area", (50.0, 50.0, 50.0, 90.0), None),
    ],
)
def test_clamps_boxes_to_the_page(name: str, corners: tuple[float, ...], expected: BBox | None):
    assert clamped_bbox(*corners, 998, 1036) == expected, name


def test_pairs_the_text_with_the_bubble_that_holds_it():
    """p0032: o modelo devolveu balao e texto como duas deteccoes da mesma fala."""
    detections = pair_detections(
        [raw("bubble", 0.967, 98, 43, 472, 476), raw("text_bubble", 0.957, 150, 133, 368, 299)],
        ARTWORK,
    )

    assert len(detections) == 1
    assert detections[0].bbox == BBox(x=98, y=43, w=472, h=476)
    assert detections[0].text_bbox == BBox(x=150, y=133, w=368, h=299)
    assert detections[0].kind == "bubble"
    assert detections[0].score == 0.967


def test_prefers_the_smaller_bubble_over_the_panel_around_it():
    painel = raw("bubble", 0.80, 0, 0, 900, 900)
    balao = raw("bubble", 0.90, 100, 100, 300, 200)
    texto = raw("text_bubble", 0.88, 120, 130, 260, 140)

    detections = pair_detections([painel, balao, texto], ARTWORK)

    paired = next(d for d in detections if d.text_bbox != d.bbox)
    assert paired.bbox == balao.bbox


def test_joins_every_text_line_of_one_bubble():
    """Duas linhas no mesmo balao viram um recorte so; descartar uma perde metade da fala."""
    detections = pair_detections(
        [
            raw("bubble", 0.95, 100, 100, 400, 300),
            raw("text_bubble", 0.90, 130, 130, 340, 60),
            raw("text_bubble", 0.85, 130, 220, 300, 60),
        ],
        ARTWORK,
    )

    assert len(detections) == 1
    assert detections[0].text_bbox == BBox(x=130, y=130, w=340, h=150)


def test_keeps_bubble_text_that_found_no_bubble():
    detections = pair_detections([raw("text_bubble", 0.72, 50, 60, 200, 90)], ARTWORK)

    assert len(detections) == 1
    assert detections[0].kind == "bubble"
    assert detections[0].bbox == detections[0].text_bbox


def test_keeps_an_empty_bubble_for_the_ocr_to_judge():
    detections = pair_detections([raw("bubble", 0.91, 50, 60, 200, 90)], ARTWORK)

    assert len(detections) == 1
    assert detections[0].bbox == detections[0].text_bbox


@pytest.mark.parametrize(
    ("name", "score", "expected"),
    [
        ("aceita texto sobre arte com confianca alta", 0.83, 1),
        ("aceita exatamente no limiar", 0.60, 1),
        ("descarta texto sobre arte com confianca baixa", 0.35, 0),
    ],
)
def test_filters_artwork_text_by_its_own_threshold(name: str, score: float, expected: int):
    """Falso positivo sobre arte desenha caixa em cima do desenho; balao perdido o motor recupera."""
    detections = pair_detections([raw("text_free", score, 73, 947, 204, 55)], ARTWORK)

    assert len(detections) == expected, name
    if expected:
        assert detections[0].kind == "free", name


def test_keeps_the_most_confident_class_when_all_three_fire_on_one_region():
    """p0012: mesma regiao saiu como text_free 0.835, text_bubble 0.254 e bubble 0.211."""
    detections = pair_detections(
        [
            raw("text_free", 0.835, 336, 636, 313, 164),
            raw("text_bubble", 0.254, 337, 635, 311, 165),
            raw("bubble", 0.211, 336, 636, 313, 164),
        ],
        ARTWORK,
    )

    assert len(detections) == 1
    assert detections[0].kind == "free"
    assert detections[0].score == 0.835


def test_keeps_neighbouring_bubbles_apart():
    detections = pair_detections(
        [raw("bubble", 0.96, 98, 43, 472, 476), raw("bubble", 0.94, 581, 834, 310, 200)],
        ARTWORK,
    )

    assert len(detections) == 2


@pytest.mark.parametrize(
    ("name", "inner", "outer", "expected"),
    [
        ("caixa inteira dentro da outra", BBox(x=10, y=10, w=10, h=10), BBox(x=0, y=0, w=100, h=100), 1.0),
        ("metade dentro", BBox(x=0, y=0, w=20, h=10), BBox(x=10, y=0, w=20, h=10), 0.5),
        ("sem interseccao", BBox(x=0, y=0, w=10, h=10), BBox(x=50, y=50, w=10, h=10), 0.0),
    ],
)
def test_measures_overlap_in_one_direction(name: str, inner: BBox, outer: BBox, expected: float):
    assert overlap_ratio(inner, outer) == pytest.approx(expected), name


def test_leaves_a_page_that_already_fits_in_one_piece():
    page = np.zeros((1487, 998, 3), dtype=np.uint8)

    strips = split_strips(page, max_height=1600, overlap=120)

    assert len(strips) == 1
    assert strips[0][1] == 0


def test_cuts_a_tall_page_with_overlap_between_strips():
    page = np.zeros((4000, 998, 3), dtype=np.uint8)

    strips = split_strips(page, max_height=1600, overlap=120)
    offsets = [offset for _, offset in strips]

    assert offsets == [0, 1480, 2960]
    assert strips[-1][0].shape[0] == 4000 - 2960
    # A sobreposicao e o que faz um balao na costura caber inteiro em alguma faixa.
    assert all(offsets[index] + 1600 > offsets[index + 1] for index in range(len(offsets) - 1))


def test_covers_the_whole_page_with_the_strips():
    page = np.zeros((4000, 998, 3), dtype=np.uint8)

    strips = split_strips(page, max_height=1600, overlap=120)
    covered = max(offset + strip.shape[0] for strip, offset in strips)

    assert covered == 4000


def test_moves_each_box_back_to_where_it_was_on_the_page():
    detections = stitch_detections(
        [
            ([raw("bubble", 0.9, 10, 20, 100, 50)], 0),
            ([raw("text_free", 0.7, 10, 30, 100, 50)], 1480),
        ]
    )

    assert [item.bbox.y for item in detections] == [20, 1510]
    assert [item.label for item in detections] == ["bubble", "text_free"]
