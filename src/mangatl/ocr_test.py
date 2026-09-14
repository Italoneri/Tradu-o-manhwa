from __future__ import annotations

import cv2
import numpy as np
import pytest

from .config import OcrConfig
from .models import BBox
from .ocr import (
    WordBox,
    _longest_word,
    clean_ocr_text,
    is_usable,
    prepare_crop,
    read_block,
    tesseract_available,
    words_to_page_bbox,
)


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("colapsa espaco e quebra de linha", "HEY\n  THERE\n", "HEY THERE"),
        ("junta palavra partida por hifen", "SOME- THING", "SOMETHING"),
        ("junta palavra partida com quebra de linha", "TOMOR-\nROW", "TOMORROW"),
        ("troca barra por I entre letras", "TH|S", "THIS"),
        ("troca barra isolada por I", "WHO AM | ?", "WHO AM I ?"),
        ("preserva hifen legitimo entre palavras", "WELL-KNOWN", "WELL-KNOWN"),
        ("remove caractere de controle", "OK\x0b\x00", "OK"),
        ("devolve vazio para entrada em branco", "   \n ", ""),
    ],
)
def test_cleans_ocr_text(name: str, raw: str, expected: str):
    assert clean_ocr_text(raw) == expected, name


@pytest.mark.parametrize(
    ("name", "text", "confidence", "expected"),
    [
        ("aceita fala curta com confianca alta", "NO", 90.0, True),
        ("aceita fala com pontuacao pesada", "WHAT?! NO...", 80.0, True),
        ("aceita fala com numero", "1000 YEARS", 85.0, True),
        ("descarta texto longo com confianca baixa", "WHAT IS GOING ON", 12.0, False),
        ("descarta simbolo solto mesmo com confianca alta", "(", 64.0, False),
        ("descarta ruido de arte com uma letra so", "e@", 48.0, False),
        ("descarta letras soltas sem formar palavra", "I I", 61.5, False),
        ("descarta letra solta com pontuacao", "r I", 49.0, False),
        ("aceita nome proprio incomum com confianca media", "RYUCHEONG?", 52.0, True),
        ("descarta texto vazio", "", 99.0, False),
    ],
)
def test_decides_usability(name: str, text: str, confidence: float, expected: bool):
    assert is_usable(text, confidence, OcrConfig()) is expected, name


def test_clamps_crop_to_image_bounds():
    image = np.full((100, 100), 255, dtype=np.uint8)
    box = BBox(x=90, y=90, w=9, h=9)

    window = prepare_crop(image, box, OcrConfig(padding=20, upscale=1))

    assert window.image.size > 0
    assert window.image.shape[0] <= 100 and window.image.shape[1] <= 100


def test_reports_the_crop_origin_clamped_to_the_page():
    # A origem e o que leva coordenada de palavra de volta para a pagina, entao ela
    # precisa ser a do recorte de fato feito, e nao a que o padding pediu.
    image = np.full((100, 100), 255, dtype=np.uint8)

    inside = prepare_crop(image, BBox(x=40, y=30, w=20, h=20), OcrConfig(padding=5, upscale=1))
    at_edge = prepare_crop(image, BBox(x=2, y=1, w=20, h=20), OcrConfig(padding=10, upscale=1))

    assert (inside.left, inside.top) == (35, 25)
    assert (at_edge.left, at_edge.top) == (0, 0)


def test_upscales_crop_by_configured_factor():
    image = np.full((100, 100), 255, dtype=np.uint8)
    box = BBox(x=10, y=10, w=40, h=20)

    window = prepare_crop(image, box, OcrConfig(padding=0, upscale=3))

    assert window.image.shape == (60, 120)


@pytest.mark.parametrize(
    ("name", "words", "origin", "upscale", "expected_bbox", "expected_font"),
    [
        (
            "uma palavra vira a propria caixa",
            [WordBox(left=10, top=20, width=100, height=40)],
            (0, 0),
            1,
            BBox(x=10, y=20, w=100, h=40),
            40,
        ),
        (
            "une as palavras de duas linhas",
            [
                WordBox(left=10, top=20, width=60, height=40),
                WordBox(left=80, top=20, width=50, height=40),
                WordBox(left=10, top=70, width=120, height=40),
            ],
            (0, 0),
            1,
            BBox(x=10, y=20, w=120, h=90),
            40,
        ),
        (
            "desfaz a ampliacao do recorte",
            [WordBox(left=30, top=60, width=300, height=120)],
            (0, 0),
            3,
            BBox(x=10, y=20, w=100, h=40),
            40,
        ),
        (
            "soma a origem do recorte",
            [WordBox(left=10, top=20, width=100, height=40)],
            (200, 300),
            1,
            BBox(x=210, y=320, w=100, h=40),
            40,
        ),
        (
            "arredonda a divisao para fora",
            # 110/3 da 36.67: truncar deixaria a serifa da ultima letra de fora.
            [WordBox(left=10, top=10, width=100, height=100)],
            (0, 0),
            3,
            BBox(x=3, y=3, w=34, h=34),
            33,
        ),
    ],
)
def test_maps_words_to_page_coordinates(
    name: str,
    words: list[WordBox],
    origin: tuple[int, int],
    upscale: int,
    expected_bbox: BBox,
    expected_font: int,
):
    origin_x, origin_y = origin

    measured = words_to_page_bbox(words, origin_x=origin_x, origin_y=origin_y, upscale=upscale)

    assert measured == (expected_bbox, expected_font), name


def test_measures_the_font_by_the_median_word_height():
    # Uma palavra com acento ou descendente mede mais alto que o corpo. A media das
    # tres daria 57; o letreiramento tem 40.
    words = [
        WordBox(left=0, top=0, width=50, height=40),
        WordBox(left=60, top=0, width=50, height=40),
        WordBox(left=120, top=0, width=50, height=90),
    ]

    measured = words_to_page_bbox(words, origin_x=0, origin_y=0, upscale=1)

    assert measured is not None
    assert measured[1] == 40


def test_reports_no_geometry_without_words():
    assert words_to_page_bbox([], origin_x=0, origin_y=0, upscale=1) is None


@pytest.mark.skipif(not tesseract_available(), reason="binario tesseract nao instalado")
def test_reads_rendered_text_from_a_bubble():
    image = np.full((200, 600), 255, dtype=np.uint8)
    cv2.putText(image, "HELLO THERE", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 4, cv2.LINE_AA)
    box = BBox(x=10, y=40, w=580, h=120)

    reading = read_block(image, box, OcrConfig())

    assert "HELLO" in reading.text.upper()
    assert reading.confidence > 0
    # A caixa do texto e mais apertada que o recorte e cai dentro dele, e o corpo
    # medido bate com a altura em que o cv2 desenhou.
    assert reading.text_bbox is not None
    assert reading.text_bbox.w < box.w
    assert box.x <= reading.text_bbox.x and reading.text_bbox.right <= box.right
    assert reading.source_font_px is not None
    assert 30 <= reading.source_font_px <= 60


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("(", 0), ("I I", 1), ("it", 2), ("WHAT?! NO...", 4), ("1000 YEARS", 5)],
)
def test_measures_the_longest_word(text: str, expected: int):
    assert _longest_word(text) == expected
