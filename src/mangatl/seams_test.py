from __future__ import annotations

import pytest

from .models import BBox
from .seams import (
    band_bbox_to_page,
    band_heights,
    covered_by_seam,
    improves_on,
    touches_bottom,
    touches_top,
)


@pytest.mark.parametrize(
    ("name", "box", "page_height", "topo", "base"),
    [
        ("caixa no meio nao encosta em nada", BBox(x=0, y=100, w=50, h=50), 400, False, False),
        ("caixa colada no topo", BBox(x=0, y=0, w=50, h=50), 400, True, False),
        ("caixa a um pixel do topo ainda conta", BBox(x=0, y=1, w=50, h=50), 400, True, False),
        ("caixa a tres pixels do topo nao conta", BBox(x=0, y=3, w=50, h=50), 400, False, False),
        ("caixa colada na base", BBox(x=0, y=350, w=50, h=50), 400, False, True),
        ("caixa a um pixel da base ainda conta", BBox(x=0, y=350, w=50, h=49), 400, False, True),
        ("caixa que cobre a pagina encosta nos dois", BBox(x=0, y=0, w=50, h=400), 400, True, True),
    ],
)
def test_finds_boxes_against_the_page_edges(
    name: str, box: BBox, page_height: int, topo: bool, base: bool
):
    assert touches_top(box) is topo, name
    assert touches_bottom(box, page_height) is base, name


@pytest.mark.parametrize(
    ("name", "upper", "lower", "fraction", "expected"),
    [
        ("toma a mesma fracao dos dois lados", 800, 1200, 0.25, (200, 300)),
        ("arredonda para o inteiro mais perto", 735, 1236, 0.25, (184, 309)),
        ("nunca toma menos que uma linha", 2, 2, 0.01, (1, 1)),
        ("nunca toma mais que a pagina", 10, 10, 0.5, (5, 5)),
    ],
)
def test_sizes_the_band_from_both_pages(
    name: str, upper: int, lower: int, fraction: float, expected: tuple[int, int]
):
    assert band_heights(upper, lower, fraction) == expected, name


def test_maps_a_crossing_box_to_the_upper_page():
    # Faixa montada com as ultimas 184 linhas de uma pagina de 735 e o inicio da
    # seguinte: a emenda cai na linha 184 da faixa. Uma caixa de y=125 a y=285
    # atravessa, e as linhas 125..183 sao as linhas 676..734 da pagina de cima.
    mapped = band_bbox_to_page(BBox(x=383, y=125, w=326, h=160), upper_take=184, upper_height=735)

    assert mapped is not None
    bbox, overflow = mapped
    assert bbox == BBox(x=383, y=676, w=326, h=59)
    assert bbox.bottom == 735, "a caixa recortada termina na base da pagina de cima"
    assert overflow == 101, "e o resto segue na pagina de baixo"


def test_splits_the_height_without_losing_a_pixel():
    mapped = band_bbox_to_page(BBox(x=0, y=50, w=10, h=200), upper_take=100, upper_height=400)

    assert mapped is not None
    bbox, overflow = mapped
    assert bbox.h + overflow == 200


@pytest.mark.parametrize(
    ("name", "bbox"),
    [
        ("caixa inteira acima da emenda", BBox(x=0, y=10, w=10, h=50)),
        ("caixa inteira abaixo da emenda", BBox(x=0, y=100, w=10, h=50)),
        ("caixa que termina exatamente na emenda", BBox(x=0, y=10, w=10, h=90)),
        ("caixa que comeca exatamente na emenda", BBox(x=0, y=100, w=10, h=50)),
    ],
)
def test_ignores_a_box_that_does_not_cross_the_seam(name: str, bbox: BBox):
    # Quem nao atravessa vive dentro de uma das paginas, que ja a detectou sozinha.
    assert band_bbox_to_page(bbox, upper_take=100, upper_height=400) is None, name


def test_recognises_the_half_left_on_the_lower_page():
    # O bloco de emenda vai de y=676 da pagina de cima (735 de altura) e sobra 101
    # pixels; a metade que a pagina de baixo detectou sozinha vai de y=0 a y=101.
    seam = BBox(x=383, y=676, w=326, h=59)
    half = BBox(x=406, y=0, w=284, h=101)

    assert covered_by_seam(half, seam, overflow=101, page_height=735, on_lower=True)


def test_recognises_the_half_left_on_the_upper_page():
    seam = BBox(x=383, y=676, w=326, h=59)
    half = BBox(x=400, y=690, w=280, h=45)

    assert covered_by_seam(half, seam, overflow=101, page_height=735, on_lower=False)


def test_keeps_a_different_speech_near_the_seam():
    # Fala vizinha, do outro lado da pagina: encosta na mesma faixa de altura e nao
    # pode ser apagada por isso.
    seam = BBox(x=383, y=676, w=326, h=59)
    other = BBox(x=20, y=0, w=200, h=90)

    assert not covered_by_seam(other, seam, overflow=101, page_height=735, on_lower=True)


@pytest.mark.parametrize(
    ("name", "seam_text", "covered", "expected"),
    [
        (
            "recupera a metade que faltava",
            "Just in case, I kept my Golden Eyes activated...",
            ["my Collen Eyes activated..."],
            True,
        ),
        (
            "trocaria frase inteira por trecho",
            "THROUGH THE DOOR THE CAT LED ME TO...",
            ["AND WHEN I STEPPED THROUGH THE DOOR THE CAT LED ME TO..."],
            False,
        ),
        ("fala que nenhuma pagina tinha lido", "WAIT!", [], True),
        (
            "empate passa: a faixa nao piorou",
            "CALL ME BY MY NAME TOO.",
            ["CALL ME BY MY NAME TOO."],
            True,
        ),
        (
            "compara com a maior das metades",
            "MEIO",
            ["oi", "uma metade bem mais longa que a costura"],
            False,
        ),
    ],
)
def test_accepts_only_a_seam_reading_that_does_not_lose_text(
    name: str, seam_text: str, covered: list[str], expected: bool
):
    assert improves_on(seam_text, covered) is expected, name
