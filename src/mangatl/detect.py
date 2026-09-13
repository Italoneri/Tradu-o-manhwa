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
from .models import BBox, BlockKind, Detection

Tagged = tuple[BBox, BlockKind]
"""Caixa com a passada que a produziu, para o kind sobreviver a fusao."""

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
    if not paper.size:
        return 0.0
    # Mediana, nao media: texto com efeito de brilho tem um halo cinza em volta
    # das letras que nao e tinta mas puxa a media para baixo. Medido numa pagina
    # real, esse halo levou o papel a 198 contra o corte de 200 e o texto foi
    # descartado por dois pontos. A mediana ignora a minoria de pixels do halo.
    return float(np.median(paper))


def _passes_shape_filters(box: BBox, contour_area: float, page_area: int, cfg: DetectConfig) -> bool:
    area_ratio = box.area / page_area
    if not (cfg.min_area_ratio <= area_ratio <= cfg.max_area_ratio):
        return False
    aspect = box.w / box.h
    if not (cfg.min_aspect <= aspect <= cfg.max_aspect):
        return False
    return (contour_area / box.area) >= cfg.min_fill_ratio


def _merge_overlapping(tagged: list[Tagged], merge_iou: float) -> list[Tagged]:
    """Funde caixas sobrepostas ou aninhadas ate estabilizar.

    Um balao com rabo ou com contorno duplo costuma render dois contornos quase
    coincidentes; sem isso o mesmo texto seria OCRado duas vezes.

    Na fusao o `bubble` vence: as duas passadas veem o mesmo balao por angulos
    diferentes, e a que sabe que ha balao em volta e a que o leitor precisa para
    decidir se pode pintar caixa.
    """
    merged = sorted(tagged, key=lambda item: item[0].area, reverse=True)
    changed = True
    while changed:
        changed = False
        result: list[Tagged] = []
        for box, kind in merged:
            for index, (kept, kept_kind) in enumerate(result):
                intersection = box.intersection_area(kept)
                contained = intersection >= 0.9 * min(box.area, kept.area)
                if contained or box.iou(kept) >= merge_iou:
                    winner = "bubble" if "bubble" in (kind, kept_kind) else kind
                    result[index] = (kept.merged_with(box), winner)
                    changed = True
                    break
            else:
                result.append((box, kind))
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
    """Aglomera tinta em blocos, para texto que nao mora dentro de balao.

    Dilatar a mascara de tinta funde letras vizinhas numa mancha por linha e por
    paragrafo. Pega legenda sem borda, SFX e texto estilizado - o que a deteccao
    de balao fechado nao tem como ver, porque nao ha balao.
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


def detect_regions(image: np.ndarray, cfg: DetectConfig) -> list[Detection]:
    """Regioes de texto de `image` (BGR ou grayscale), sem ordem definida.

    A ordem de leitura e responsabilidade de `ordering.reading_order`.

    `bbox` e `text_bbox` saem iguais: a heuristica enxerga um retangulo so por
    regiao, e nao tem como separar o contorno do balao do texto dentro dele.
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, INK_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    # Os dois detectores sempre, e nao um como reserva do outro: texto sem balao
    # convive com balao na mesma pagina, e enquanto isto era um fallback ele so
    # rodava em paginas totalmente vazias - bastava um balao para o texto solto
    # da mesma pagina ficar invisivel. O excesso de candidato e barato porque o
    # OCR e quem filtra.
    tagged: list[Tagged] = [(box, "bubble") for box in _detect_enclosed_bubbles(gray, ink, cfg)]
    tagged += [(box, "free") for box in _detect_text_blobs(gray, ink, cfg)]

    return [
        Detection(bbox=box, text_bbox=box, kind=kind)
        for box, kind in _merge_overlapping(tagged, cfg.merge_iou)
    ]


def detect_bubbles(image: np.ndarray, cfg: DetectConfig) -> list[BBox]:
    """So as caixas de `detect_regions`, para quem nao se importa com a classe."""
    return [detection.bbox for detection in detect_regions(image, cfg)]


KIND_COLOR: dict[BlockKind, tuple[int, int, int]] = {"bubble": (60, 180, 60), "free": (220, 140, 40)}
DROPPED_COLOR = (60, 60, 220)
TEXT_BBOX_COLOR = (200, 200, 60)


def draw_boxes(image: np.ndarray, kept: list[Detection], dropped: list[BBox] = []) -> np.ndarray:
    """Copia de `image` com as caixas marcadas, para calibrar a deteccao a olho.

    Verde numerado e fala dentro de balao; azul numerado e fala sobre a arte, que
    o leitor nao pode tapar com caixa branca; vermelho e o que o detector achou e
    o filtro de OCR descartou. Separar os tres e o que diz qual ajuste fazer:
    muito vermelho significa deteccao solta demais, e balao sem caixa nenhuma
    significa o contrario.

    Quando a caixa do texto difere a do balao, ela aparece fina por dentro - e o
    que torna visivel se o pareamento das duas classes esta certo.
    """
    canvas = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    for box in dropped:
        cv2.rectangle(canvas, (box.x, box.y), (box.right, box.bottom), DROPPED_COLOR, 2)

    for position, detection in enumerate(kept, start=1):
        box, color = detection.bbox, KIND_COLOR[detection.kind]
        cv2.rectangle(canvas, (box.x, box.y), (box.right, box.bottom), color, 3)
        if detection.text_bbox != box:
            text_box = detection.text_bbox
            cv2.rectangle(
                canvas, (text_box.x, text_box.y), (text_box.right, text_box.bottom),
                TEXT_BBOX_COLOR, 1,
            )
        cv2.putText(
            canvas, f"{position} {detection.score:.2f}", (box.x + 4, box.y + 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2, cv2.LINE_AA,
        )
    return canvas
