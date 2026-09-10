from __future__ import annotations

import cv2
import numpy as np
import pytest

from .config import OcrConfig
from .models import BBox
from .ocr import _longest_word, clean_ocr_text, is_usable, prepare_crop, read_block, tesseract_available


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

    crop = prepare_crop(image, box, OcrConfig(padding=20, upscale=1))

    assert crop.size > 0
    assert crop.shape[0] <= 100 and crop.shape[1] <= 100


def test_upscales_crop_by_configured_factor():
    image = np.full((100, 100), 255, dtype=np.uint8)
    box = BBox(x=10, y=10, w=40, h=20)

    crop = prepare_crop(image, box, OcrConfig(padding=0, upscale=3))

    assert crop.shape == (60, 120)


@pytest.mark.skipif(not tesseract_available(), reason="binario tesseract nao instalado")
def test_reads_rendered_text_from_a_bubble():
    image = np.full((200, 600), 255, dtype=np.uint8)
    cv2.putText(image, "HELLO THERE", (30, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 4, cv2.LINE_AA)

    text, confidence = read_block(image, BBox(x=10, y=40, w=580, h=120), OcrConfig())

    assert "HELLO" in text.upper()
    assert confidence > 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("(", 0), ("I I", 1), ("it", 2), ("WHAT?! NO...", 4), ("1000 YEARS", 5)],
)
def test_measures_the_longest_word(text: str, expected: int):
    assert _longest_word(text) == expected
