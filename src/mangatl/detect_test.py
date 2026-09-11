from __future__ import annotations

import cv2
import numpy as np
import pytest

from .config import DetectConfig
from .detect import detect_bubbles
from .models import BBox

PAGE_W, PAGE_H = 900, 1300
BACKGROUND = 150  # arte: claro o bastante para ser visivel, escuro para nao virar balao


def blank_page() -> np.ndarray:
    return np.full((PAGE_H, PAGE_W), BACKGROUND, dtype=np.uint8)


def draw_bubble(page: np.ndarray, box: BBox, *, lines: int = 4) -> None:
    """Balao branco com texto renderizado de verdade.

    Barras solidas dariam densidade de tinta irreal (100% da linha, contra ~15%
    de glifo real) e fariam o teste calibrar os thresholds no valor errado.
    """
    cv2.rectangle(page, (box.x, box.y), (box.right, box.bottom), 255, -1)
    cv2.rectangle(page, (box.x, box.y), (box.right, box.bottom), 0, 3)
    for line in range(lines):
        baseline = box.y + 40 + line * 32
        if baseline > box.bottom - 12:
            break
        cv2.putText(
            page, "ALGUMA FALA AQUI", (box.x + 16, baseline),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 2, cv2.LINE_AA,
        )


def matches(found: list[BBox], expected: BBox, *, min_iou: float = 0.5) -> bool:
    return any(box.iou(expected) >= min_iou for box in found)


@pytest.fixture
def cfg() -> DetectConfig:
    return DetectConfig()


def test_finds_no_bubbles_on_empty_page(cfg: DetectConfig):
    assert detect_bubbles(blank_page(), cfg) == []


def test_finds_a_single_bubble(cfg: DetectConfig):
    page = blank_page()
    expected = BBox(x=100, y=120, w=320, h=180)
    draw_bubble(page, expected)

    assert matches(detect_bubbles(page, cfg), expected)


def test_finds_every_bubble_on_a_busy_page(cfg: DetectConfig):
    page = blank_page()
    expected = [
        BBox(x=60, y=80, w=300, h=170),
        BBox(x=480, y=110, w=340, h=190),
        BBox(x=200, y=700, w=380, h=200),
    ]
    for box in expected:
        draw_bubble(page, box)

    found = detect_bubbles(page, cfg)
    assert all(matches(found, box) for box in expected)


def test_rejects_empty_bubble_without_text(cfg: DetectConfig):
    page = blank_page()
    empty = BBox(x=100, y=120, w=320, h=180)
    cv2.rectangle(page, (empty.x, empty.y), (empty.right, empty.bottom), 255, -1)
    cv2.rectangle(page, (empty.x, empty.y), (empty.right, empty.bottom), 0, 3)

    assert not matches(detect_bubbles(page, cfg), empty)


def test_rejects_full_page_white_background(cfg: DetectConfig):
    page = np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)
    bubble = BBox(x=100, y=120, w=320, h=180)
    draw_bubble(page, bubble)

    found = detect_bubbles(page, cfg)
    page_area = PAGE_W * PAGE_H
    assert all(box.area < cfg.max_area_ratio * page_area for box in found)


def test_merges_duplicate_contours_of_one_bubble(cfg: DetectConfig):
    page = blank_page()
    box = BBox(x=100, y=120, w=320, h=180)
    draw_bubble(page, box)
    # contorno duplo, como balao com borda dupla
    cv2.rectangle(page, (box.x - 6, box.y - 6), (box.right + 6, box.bottom + 6), 0, 3)

    found = detect_bubbles(page, cfg)
    assert len([b for b in found if b.iou(box) >= 0.5]) == 1


def test_accepts_bgr_and_grayscale_alike(cfg: DetectConfig):
    page = blank_page()
    box = BBox(x=100, y=120, w=320, h=180)
    draw_bubble(page, box)

    from_gray = detect_bubbles(page, cfg)
    from_bgr = detect_bubbles(cv2.cvtColor(page, cv2.COLOR_GRAY2BGR), cfg)
    assert from_gray == from_bgr


def white_page() -> np.ndarray:
    return np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)


def test_runs_both_detectors_even_when_the_bubble_pass_finds_something(monkeypatch, cfg: DetectConfig):
    """O detector de blocos era reserva do de baloes.

    Enquanto era reserva, bastava um balao na pagina para o texto solto da mesma
    pagina ficar invisivel - foi assim que uma legenda estilizada se perdeu numa
    pagina que tinha balao logo acima.
    """
    from . import detect as modulo

    balao = BBox(x=60, y=80, w=300, h=170)
    solto = BBox(x=400, y=700, w=280, h=120)
    monkeypatch.setattr(modulo, "_detect_enclosed_bubbles", lambda *_: [balao])
    monkeypatch.setattr(modulo, "_detect_text_blobs", lambda *_: [solto])

    found = detect_bubbles(white_page(), cfg)

    assert balao in found
    assert solto in found


def test_still_uses_the_blob_detector_when_no_bubble_is_found(monkeypatch, cfg: DetectConfig):
    from . import detect as modulo

    solto = BBox(x=400, y=700, w=280, h=120)
    monkeypatch.setattr(modulo, "_detect_enclosed_bubbles", lambda *_: [])
    monkeypatch.setattr(modulo, "_detect_text_blobs", lambda *_: [solto])

    assert detect_bubbles(white_page(), cfg) == [solto]


def test_measures_paper_brightness_from_the_median_not_the_mean():
    """Halo de brilho e minoria de pixels; nao pode decidir a cor do papel.

    Numeros do caso real: o halo puxou a media para 198 contra o corte de 200 e
    o texto foi descartado por dois pontos.
    """
    from .detect import _paper_brightness

    region = np.full((100, 100), 255, dtype=np.uint8)
    region[:45, :] = 120  # halo cinza em volta das letras
    ink = np.zeros((100, 100), dtype=np.uint8)
    box = BBox(x=0, y=0, w=100, h=100)

    assert region.mean() < 200
    assert _paper_brightness(region, ink, box) == 255
