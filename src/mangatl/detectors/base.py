"""Interface dos detectores de regiao de texto.

Um detector recebe a imagem de uma pagina e devolve regioes. Ele nao le disco,
nao conhece config de OCR e nao decide ordem de leitura - por isso trocar
`--detector` nao toca em nenhuma outra parte do pipeline.

Os dois backends resolvem o mesmo problema por caminhos opostos: o `heuristic` e
visao classica e enxerga um retangulo por regiao; o `rtdetr` e um modelo treinado
e separa o contorno do balao do texto dentro dele. O `rtdetr` ve balao colorido e
balao sem borda, que sao invisiveis para o `heuristic` por construcao - ele exige
papel branco.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

import numpy as np

from ..config import Config
from ..models import Detection


class Detector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...


class UnknownDetectorError(ValueError):
    pass


class DetectorUnavailableError(RuntimeError):
    """Backend existe mas nao pode rodar aqui - dependencia ou modelo ausente."""


def _make_heuristic(cfg: Config) -> Detector:
    from .heuristic import HeuristicDetector

    return HeuristicDetector(cfg)


def _make_rtdetr(cfg: Config) -> Detector:
    from .rtdetr import RtdetrDetector

    return RtdetrDetector(cfg)


_FACTORIES: Mapping[str, Callable[[Config], Detector]] = {
    "heuristic": _make_heuristic,
    "rtdetr": _make_rtdetr,
}
"""Import tardio de proposito: o rtdetr arrasta torch, e quem so usa o heuristic
nao deve precisar dele instalado para rodar o pipeline."""


def available_detectors() -> list[str]:
    return sorted(_FACTORIES)


def create_detector(name: str, cfg: Config) -> Detector:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise UnknownDetectorError(
            f"detector '{name}' nao existe; disponiveis: {', '.join(available_detectors())}"
        )
    return factory(cfg)
