"""Deteccao de baloes de fala numa pagina de HQ.

Heuristica em vez de modelo treinado: um balao e uma regiao clara, fechada,
razoavelmente convexa, com uma quantidade moderada de tinta dentro. Cada uma
dessas quatro palavras vira um threshold em [detect] no config.toml, e
`--debug-boxes` desenha o resultado para voce calibrar olhando.

O custo dessa escolha e conhecido: SFX estilizado e texto flutuante sem balao
escapam. Com o motor `claude`, que recebe a imagem da pagina, essas falas
voltam com bbox=None em vez de sumirem.
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import DetectConfig
from .models import BBox

LIGHT_THRESHOLD = 200
"""Acima disso o pixel conta como interior de balao branco."""

INK_THRESHOLD = 100
"""Abaixo disso o pixel conta como tinta (texto ou traco)."""


def _bbox_of(contour: np.ndarray) -> BBox:
    x, y, w, h = cv2.boundingRect(contour)
    return BBox(x=int(x), y=int(y), w=int(w), h=int(h))


def _ink_ratio(ink_mask: np.ndarray, box: BBox) -> float:
    region = ink_mask[box.y : box.bottom, box.x : box.right]
    if region.size == 0:
        return 0.0
    return float(np.count_nonzero(region)) / region.size


def _inset(box: BBox, fraction: float = 0.08, minimum: int = 3) -> BBox:
    """Encolhe a caixa para excluir o proprio contorno do balao.

    O contorno e tinta, e sem isso um balao vazio passa no teste de "tem texto"
    so pela borda que o desenha.
    """
    margin_x = max(minimum, round(box.w * fraction))
    margin_y = max(minimum, round(box.h * fraction))
    if box.w - 2 * margin_x < 1 or box.h - 2 * margin_y < 1:
        return box
    return BBox(x=box.x + margin_x, y=box.y + margin_y, w=box.w - 2 * margin_x, h=box.h - 2 * margin_y)


def _paper_brightness(gray: np.ndarray, ink_mask: np.ndarray, box: BBox) -> float:
    """Brilho do fundo do balao, ignorando o texto.

    Medir a media da caixa inteira confunde "balao branco com muito texto" com
    "regiao escura de arte": o texto derruba a media exatamente onde ele deveria
    ser evidencia a favor.
    """
    region = gray[box.y : box.bottom, box.x : box.right]
    ink = ink_mask[box.y : box.bottom, box.x : box.right]
    paper = region[ink == 0]
    return float(paper.mean()) if paper.size else 0.0


def _passes_shape_filters(box: BBox, contour_area: float, page_area: int, cfg: DetectConfig) -> bool:
    area_ratio = box.area / page_area
    if not (cfg.min_area_ratio <= area_ratio <= cfg.max_area_ratio):
        return False
    aspect = box.w / box.h
    if not (cfg.min_aspect <= aspect <= cfg.max_aspect):
        return False
    return (contour_area / box.area) >= cfg.min_fill_ratio


def _merge_overlapping(boxes: list[BBox], merge_iou: float) -> list[BBox]:
    """Funde caixas sobrepostas ou aninhadas ate estabilizar.

    Um balao com rabo ou com contorno duplo costuma render dois contornos quase
    coincidentes; sem isso o mesmo texto seria OCRado duas vezes.
    """
    merged = sorted(boxes, key=lambda b: b.area, reverse=True)
    changed = True
    while changed:
        changed = False
        result: list[BBox] = []
        for box in merged:
            for index, kept in enumerate(result):
                intersection = box.intersection_area(kept)
                contained = intersection >= 0.9 * min(box.area, kept.area)
                if contained or box.iou(kept) >= merge_iou:
                    result[index] = kept.merged_with(box)
                    changed = True
                    break
            else:
                result.append(box)
        merged = result
    return merged


def _detect_enclosed_bubbles(gray: np.ndarray, ink: np.ndarray, cfg: DetectConfig) -> list[BBox]:
    _, light = cv2.threshold(gray, LIGHT_THRESHOLD, 255, cv2.THRESH_BINARY)
    light = cv2.morphologyEx(light, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    contours, hierarchy = cv2.findContours(light, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []

    page_area = gray.shape[0] * gray.shape[1]
    candidates: list[BBox] = []
    for contour, meta in zip(contours, hierarchy[0], strict=True):
        is_outer = meta[3] == -1
        if not is_outer:
            continue
        box = _bbox_of(contour)
        if not _passes_shape_filters(box, cv2.contourArea(contour), page_area, cfg):
            continue
        if _paper_brightness(gray, ink, box) < cfg.min_interior_brightness:
            continue
        if not (cfg.min_ink_ratio <= _ink_ratio(ink, _inset(box)) <= cfg.max_ink_ratio):
            continue
        candidates.append(box)
    return candidates


def _detect_text_blobs(gray: np.ndarray, ink: np.ndarray, cfg: DetectConfig) -> list[BBox]:
    """Fallback para paginas sem balao fechado: aglomera tinta em blocos de texto.

    Dilatar a mascara de tinta funde letras vizinhas numa mancha por linha e por
    paragrafo. Pega legenda sem borda; erra em arte densa, por isso so roda
    quando a deteccao principal nao achou nada.
    """
    horizontal = max(3, gray.shape[1] // 60)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal, 5))
    blobs = cv2.dilate(ink, kernel, iterations=2)

    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = gray.shape[0] * gray.shape[1]

    candidates: list[BBox] = []
    for contour in contours:
        box = _bbox_of(contour)
        area_ratio = box.area / page_area
        if not (cfg.min_area_ratio <= area_ratio <= cfg.max_area_ratio):
            continue
        # Inset profundo de proposito: bloco de texto tem tinta no miolo, contorno
        # de balao vazio so tem tinta na borda. Sem isso a dilatacao transforma a
        # borda de um balao sem texto num falso bloco.
        if _ink_ratio(ink, _inset(box, fraction=0.25)) < cfg.min_ink_ratio:
            continue
        if _paper_brightness(gray, ink, box) < cfg.min_interior_brightness:
            continue
        candidates.append(box)
    return candidates


def detect_bubbles(image: np.ndarray, cfg: DetectConfig) -> list[BBox]:
    """Caixas dos baloes de `image` (BGR ou grayscale), sem ordem definida.

    A ordem de leitura e responsabilidade de `ordering.reading_order`.
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, INK_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    boxes = _detect_enclosed_bubbles(gray, ink, cfg)
    if not boxes:
        boxes = _detect_text_blobs(gray, ink, cfg)
    return _merge_overlapping(boxes, cfg.merge_iou)


def draw_boxes(image: np.ndarray, boxes: list[BBox]) -> np.ndarray:
    """Copia de `image` com as caixas numeradas na ordem recebida."""
    canvas = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    for position, box in enumerate(boxes, start=1):
        cv2.rectangle(canvas, (box.x, box.y), (box.right, box.bottom), (0, 0, 255), 2)
        cv2.putText(
            canvas, str(position), (box.x + 4, box.y + 24),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA,
        )
    return canvas
