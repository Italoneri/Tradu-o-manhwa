"""Adaptador do detector de visao classica para a interface `Detector`.

Nao reimplementa nada: `detect.detect_regions` continua sendo a implementacao.
"""

from __future__ import annotations

import numpy as np

from ..config import Config
from ..detect import detect_regions
from ..models import Detection


class HeuristicDetector:
    name = "heuristic"

    def __init__(self, cfg: Config) -> None:
        self._detect_config = cfg.detect

    def detect(self, image: np.ndarray) -> list[Detection]:
        return detect_regions(image, self._detect_config)
