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
    cv2.rectangle(page, (box.x, box.y), (box.right, box.bottom), 255, -1)
    cv2.rectangle(page, (box.x, box.y), (box.right, box.bottom), 0, 3)
    for line in range(lines):
        top = box.y + 24 + line * 32
        cv2.rectangle(page, (box.x + 20, top), (box.right - 20, top + 14), 0, -1)


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
