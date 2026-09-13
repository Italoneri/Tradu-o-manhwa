"""Deteccao com RT-DETR-v2 treinado em HQ (`ogkalu/comic-text-and-bubble-detector`).

O modelo devolve tres classes: `bubble` (o contorno inteiro do balao),
`text_bubble` (so o texto dentro dele) e `text_free` (texto sobre a arte - SFX,
narracao sem moldura, placa).

A heuristica que este backend substitui exige papel branco para reconhecer um
balao, entao balao colorido, balao sem borda e texto sobre arte eram invisiveis
para ela por construcao, nao por calibracao ruim.

As funcoes puras daqui - pareamento, dedup, faixas - sao testadas sem carregar o
modelo, porque a carga custa segundos e baixa centenas de MB.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import Config, RtdetrConfig
from ..models import BBox, Detection
from .base import DetectorUnavailableError

TEXT_INSIDE_BUBBLE = 0.7
"""Fracao do `text_bubble` que precisa cair dentro do `bubble` para parear."""

DUPLICATE_OVERLAP = 0.5
"""Sobreposicao, em qualquer um dos dois sentidos, que ja denuncia duplicata."""


@dataclass(frozen=True)
class RawDetection:
    """Uma caixa como o modelo a devolveu, antes de virar `Detection`."""

    label: str
    score: float
    bbox: BBox


def clamped_bbox(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> BBox | None:
    """Caixa em cantos -> `BBox`, presa aos limites da imagem.

    O modelo extrapola a borda: numa pagina real ele devolveu y1 = -1 para um
    titulo colado no topo, e `BBox` rejeita coordenada negativa. Devolve None
    quando o que sobra depois do corte nao tem area.
    """
    left, right = sorted((round(x1), round(x2)))
    top, bottom = sorted((round(y1), round(y2)))
    left, right = max(0, left), min(width, right)
    top, bottom = max(0, top), min(height, bottom)
    if right <= left or bottom <= top:
        return None
    return BBox(x=left, y=top, w=right - left, h=bottom - top)


def overlap_ratio(inner: BBox, outer: BBox) -> float:
    """Fracao de `inner` coberta por `outer`."""
    if inner.area == 0:
        return 0.0
    return inner.intersection_area(outer) / inner.area


def _dedupe(detections: Sequence[Detection]) -> list[Detection]:
    """Mantem a de maior score entre as que descrevem a mesma regiao.

    Sobreposicao nos dois sentidos, nao IoU: as tres classes disparam juntas na
    mesma regiao, e uma caixa aninhada na outra tem IoU baixo e mesmo assim e
    duplicata. Medido numa pagina real, o mesmo bloco saiu como text_free 0.835,
    text_bubble 0.254 e bubble 0.211.
    """
    kept: list[Detection] = []
    for detection in sorted(detections, key=lambda d: d.score, reverse=True):
        duplicate = any(
            overlap_ratio(detection.bbox, other.bbox) > DUPLICATE_OVERLAP
            or overlap_ratio(other.bbox, detection.bbox) > DUPLICATE_OVERLAP
            for other in kept
        )
        if not duplicate:
            kept.append(detection)
    return kept


def pair_detections(raw: Sequence[RawDetection], artwork_confidence: float) -> list[Detection]:
    """Casa cada `text_bubble` com o `bubble` que o contem.

    Aqui divergimos da referencia de proposito: ela deduplica e joga a menor
    caixa fora, nos guardamos as duas. Sao dois consumidores diferentes - o OCR
    quer o recorte apertado do texto, o overlay quer o retangulo do balao - e
    ficar com uma so obrigaria a escolher entre OCR pior e overlay pior.

    Quando ha mais de um balao candidato vence o de menor area: e o balao, nao o
    painel que o contem. Quando o mesmo balao recebe varios `text_bubble` eles
    viram um so, pela uniao - sao as linhas da mesma fala, e descartar uma
    perderia metade do dialogo.
    """
    bubbles = [item for item in raw if item.label == "bubble"]
    texts = [item for item in raw if item.label == "text_bubble"]

    paired: dict[int, list[RawDetection]] = {}
    orphan_texts: list[RawDetection] = []
    for text in texts:
        candidates = [
            index
            for index, bubble in enumerate(bubbles)
            if overlap_ratio(text.bbox, bubble.bbox) > TEXT_INSIDE_BUBBLE
        ]
        if not candidates:
            orphan_texts.append(text)
            continue
        paired.setdefault(min(candidates, key=lambda index: bubbles[index].bbox.area), []).append(text)

    detections: list[Detection] = []
    for index, bubble in enumerate(bubbles):
        inside = paired.get(index, [])
        if not inside:
            detections.append(Detection(bbox=bubble.bbox, text_bbox=bubble.bbox, score=bubble.score))
            continue
        text_bbox = inside[0].bbox
        for extra in inside[1:]:
            text_bbox = text_bbox.merged_with(extra.bbox)
        detections.append(
            Detection(
                bbox=bubble.bbox,
                text_bbox=text_bbox,
                score=max(bubble.score, *(item.score for item in inside)),
            )
        )

    # Texto de balao sem balao em volta acontece quando o contorno e fraco demais
    # para o modelo enxergar. A fala esta la; so nao ha retangulo melhor que ela.
    detections += [
        Detection(bbox=text.bbox, text_bbox=text.bbox, score=text.score) for text in orphan_texts
    ]

    # Texto sobre arte exige confianca maior: falso positivo ali desenha caixa em
    # cima do desenho, o que e pior que perder um balao - balao perdido o motor
    # claude ainda recupera a partir da imagem, com bbox nulo.
    detections += [
        Detection(bbox=item.bbox, text_bbox=item.bbox, kind="free", score=item.score)
        for item in raw
        if item.label == "text_free" and item.score >= artwork_confidence
    ]

    return _dedupe(detections)


def split_strips(image: np.ndarray, max_height: int, overlap: int) -> list[tuple[np.ndarray, int]]:
    """Faixas horizontais da imagem com o deslocamento de cada uma.

    A sobreposicao existe para que um balao na costura caiba inteiro em pelo
    menos uma das faixas; sem ela o modelo veria dois meios-baloes e nao
    reconheceria nenhum. A imagem original nunca e tocada.
    """
    height = image.shape[0]
    if height <= max_height:
        return [(image, 0)]

    step = max(1, max_height - overlap)
    strips: list[tuple[np.ndarray, int]] = []
    top = 0
    while top < height:
        bottom = min(top + max_height, height)
        strips.append((image[top:bottom], top))
        if bottom >= height:
            break
        top += step
    return strips


def stitch_detections(per_strip: Sequence[tuple[Sequence[RawDetection], int]]) -> list[RawDetection]:
    """Remapeia as caixas de cada faixa para a pagina inteira.

    A dedup fica para depois do pareamento: aqui as classes ainda estao soltas, e
    fundir um `bubble` de uma faixa com o `text_bubble` da outra apagaria
    justamente a distincao que este backend existe para trazer.
    """
    return [
        RawDetection(
            label=item.label,
            score=item.score,
            bbox=item.bbox.model_copy(update={"y": item.bbox.y + y_offset}),
        )
        for items, y_offset in per_strip
        for item in items
    ]


_lock = threading.Lock()
_loaded: dict[str, Any] = {}


def _resolve_device(requested: str) -> str:
    import torch

    if requested != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load(cfg: RtdetrConfig) -> tuple[Any, Any, str]:
    """Modelo, processor e device, carregados uma vez por processo.

    A carga custa segundos e um capitulo tem 155 paginas; recarregar por pagina
    dominaria o tempo total.
    """
    if _loaded:
        return _loaded["model"], _loaded["processor"], _loaded["device"]

    with _lock:
        if _loaded:
            return _loaded["model"], _loaded["processor"], _loaded["device"]
        try:
            import torch  # noqa: F401 - `_resolve_device` precisa dele carregado
            import transformers
        except ImportError as error:
            raise DetectorUnavailableError(
                "detector 'rtdetr' precisa de torch e transformers: pip install -e '.[rtdetr]'"
            ) from error

        # O processor em PIL evita a dependencia de torchvision, que o
        # RTDetrImageProcessor padrao exige e que so serve para redimensionar.
        processor_class = getattr(transformers, "RTDetrImageProcessorPil", None)
        if processor_class is None:
            processor_class = transformers.RTDetrImageProcessor

        device = _resolve_device(cfg.device)
        _loaded["processor"] = processor_class.from_pretrained(cfg.model_id)
        _loaded["model"] = transformers.RTDetrV2ForObjectDetection.from_pretrained(cfg.model_id).to(device).eval()
        _loaded["device"] = device

    return _loaded["model"], _loaded["processor"], _loaded["device"]


class RtdetrDetector:
    name = "rtdetr"

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg.detect.rtdetr

    def _infer(self, strip: np.ndarray) -> list[RawDetection]:
        import torch
        from PIL import Image

        model, processor, device = _load(self._cfg)
        height, width = strip.shape[:2]
        page = Image.fromarray(strip[:, :, ::-1] if strip.ndim == 3 else strip)

        inputs = processor(images=page, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
        results = processor.post_process_object_detection(
            outputs,
            target_sizes=torch.tensor([(height, width)], device=device),
            threshold=self._cfg.confidence,
        )[0]

        raw: list[RawDetection] = []
        for score, label_id, box in zip(results["scores"], results["labels"], results["boxes"], strict=True):
            bbox = clamped_bbox(*(float(value) for value in box), width, height)
            if bbox is None:
                continue
            raw.append(
                RawDetection(label=model.config.id2label[int(label_id)], score=float(score), bbox=bbox)
            )
        return raw

    def detect(self, image: np.ndarray) -> list[Detection]:
        strips = split_strips(image, self._cfg.max_strip_height, self._cfg.strip_overlap)
        per_strip = [(self._infer(strip), y_offset) for strip, y_offset in strips]
        return pair_detections(stitch_detections(per_strip), self._cfg.artwork_confidence)
