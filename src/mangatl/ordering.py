"""Ordem de leitura de baloes numa pagina de HQ.

Baloes nao vem numa grade. A heuristica que funciona em pagina ocidental
(esquerda->direita, cima->baixo) e agrupar por faixa horizontal antes de ordenar
por x: baloes que dividem a mesma "linha" visual sao lidos juntos, mesmo que seus
topos nao estejam alinhados.

Modulo puro, sem I/O - toda a incerteza de layout mora aqui e e testavel sozinha.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import BBox


def _vertical_overlap_ratio(box: BBox, band_top: int, band_bottom: int) -> float:
    """Fracao da altura menor (caixa ou faixa) coberta pela sobreposicao vertical.

    Normalizar pela menor das duas alturas evita que um balao alto seja recusado
    por uma faixa baixa, e vice-versa.
    """
    overlap = min(box.bottom, band_bottom) - max(box.y, band_top)
    if overlap <= 0:
        return 0.0
    return overlap / min(box.h, band_bottom - band_top)


def reading_order(
    boxes: Sequence[BBox],
    *,
    band_overlap: float = 0.40,
    rtl: bool = False,
) -> list[int]:
    """Indices de `boxes` na ordem de leitura.

    Devolve indices em vez das caixas para que o chamador reordene qualquer lista
    paralela (texto, confianca, id) sem precisar casar objetos de volta.
    """
    if not boxes:
        return []

    by_position = sorted(range(len(boxes)), key=lambda i: (boxes[i].y, boxes[i].x))

    bands: list[list[int]] = []
    band_top = boxes[by_position[0]].y
    band_bottom = boxes[by_position[0]].bottom
    current: list[int] = []

    for index in by_position:
        box = boxes[index]
        if current and _vertical_overlap_ratio(box, band_top, band_bottom) < band_overlap:
            bands.append(current)
            current = []
            band_top, band_bottom = box.y, box.bottom
        current.append(index)
        band_top = min(band_top, box.y)
        band_bottom = max(band_bottom, box.bottom)

    bands.append(current)

    ordered: list[int] = []
    for band in bands:
        ordered.extend(sorted(band, key=lambda i: boxes[i].x, reverse=rtl))
    return ordered
