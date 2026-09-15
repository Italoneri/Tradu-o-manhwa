"""Fatiamento de capturas de rolagem (webtoon) em paginas processaveis.

Uma captura costurada chega com proporcao extrema - 1004x29799 e comum. Isso
quebra o pipeline em tres pontos independentes:

- a imagem enviada a API e reduzida ao lado maximo, e uma pagina de 30000px de
  altura vira ~50px de largura: o texto deixa de existir para o modelo;
- os filtros de area em [detect] sao proporcionais a area da pagina, entao numa
  pagina 20x mais alta o balao minimo aceito fica 20x maior;
- cada pagina ocupa mais de 100MB descomprimida.

Fatiar resolve os tres de uma vez, e as fatias viram paginas normais - nenhuma
outra parte do pipeline precisa saber que isso aconteceu.

O corte nao pode cair em qualquer altura: cortar no meio de um balao produz duas
metades que o OCR le como ruido. Por isso a costura e procurada onde ha menos
tinta, perto da altura alvo.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from itertools import pairwise
from math import ceil
from pathlib import Path

import cv2
import numpy as np

from .config import SlicingConfig
from .store import SOURCE_DIRNAME

log = logging.getLogger("mangatl.slicing")

INK_THRESHOLD = 100
"""Abaixo disso o pixel conta como tinta. Mesmo criterio de detect.py."""



def is_tall(width: int, height: int, cfg: SlicingConfig) -> bool:
    return height > cfg.max_height and (height / width) >= cfg.tall_ratio


def ink_per_row(gray: np.ndarray, margin: int = 0) -> np.ndarray:
    """Quantidade de tinta em cada linha, ignorando `margin` colunas de cada lado.

    A margem existe por causa da moldura da captura. Medido na captura de teste,
    as quatro primeiras colunas eram pretas em 100% das linhas: uma borda de 4px
    punha tinta em toda linha da pagina e nenhuma linha ficava zerada. Descontando
    a moldura, 46% das linhas viram tinta zero - e a diferenca entre calha e
    interior de balao volta a existir, porque o contorno do balao fica bem longe
    da borda e continua contando como tinta.
    """
    region = gray[:, margin : gray.shape[1] - margin] if margin else gray
    return (region < INK_THRESHOLD).sum(axis=1)


def frame_margin(width: int) -> int:
    """Colunas de cada lado descartadas como moldura da captura."""
    return max(4, round(width * 0.01))


def _empty_runs(quiet: np.ndarray) -> list[tuple[int, int]]:
    """Faixas continuas de linhas silenciosas, como (inicio, comprimento)."""
    runs: list[tuple[int, int]] = []
    start = None
    for offset, is_quiet in enumerate(quiet):
        if is_quiet and start is None:
            start = offset
        elif not is_quiet and start is not None:
            runs.append((start, offset - start))
            start = None
    if start is not None:
        runs.append((start, len(quiet) - start))
    return runs


def quiet_level(ink: np.ndarray, width: int) -> int:
    """Quanta tinta numa linha ainda conta como calha entre paineis.

    Comparar com zero nao serve em pagina real: uma borda ou marca d'agua de poucos
    pixels poe tinta em toda linha da imagem, e nenhuma linha fica exatamente vazia.
    Medido na captura de teste, a linha mais limpa tinha 4 pixels de tinta e nenhuma
    tinha zero - com o teste absoluto, a busca por calha nunca disparava.
    """
    return int(ink.min()) + max(2, round(width * 0.005))


def _quietest_row(ink: np.ndarray, low: int, high: int, level: int) -> int:
    """Melhor linha de corte dentro de [low, high).

    Uma faixa continua sem tinta e a calha entre paineis: cortar no meio dela e
    invisivel. Sem calha, a linha com menos tinta e o menor dano possivel - ela
    nunca cai sobre texto, que e denso.

    Empate resolve pela proximidade do centro da janela, que e a altura alvo.
    Sem isso, uma tira de densidade uniforme corta sempre na borda da janela e
    produz fatias desiguais.
    """
    window = ink[low:high]
    if window.size == 0:
        return low

    centre = window.size // 2
    quiet = window <= level
    if quiet.any():
        start, length = max(
            _empty_runs(quiet),
            key=lambda run: (run[1], -abs(run[0] + run[1] // 2 - centre)),
        )
        return low + start + length // 2

    quietest = np.flatnonzero(window == window.min())
    return low + int(quietest[np.argmin(np.abs(quietest - centre))])


def plan_cuts(gray: np.ndarray, cfg: SlicingConfig) -> list[tuple[int, int]]:
    """Faixas (topo, base) que cobrem a imagem inteira, sem sobreposicao nem buraco."""
    height = gray.shape[0]
    if height <= cfg.max_height:
        return [(0, height)]

    # O corte se afasta do alvo em ate uma janela para cada lado, entao a fatia
    # pode chegar a nominal*(1 + 2*seam_window). Dividir o alvo por esse fator faz
    # max_height ser de fato o maximo - e e o que garante que uma fatia nunca volte
    # a ser considerada alta, tornando o fatiamento idempotente.
    target = cfg.max_height / (1 + 2 * cfg.seam_window)
    slices_needed = ceil(height / target)
    nominal = height / slices_needed
    window = max(1, round(nominal * cfg.seam_window))
    width = gray.shape[1]
    ink = ink_per_row(gray, frame_margin(width))
    level = quiet_level(ink, width)

    cuts: list[int] = []
    for position in range(1, slices_needed):
        target = round(position * nominal)
        low = max(cuts[-1] + cfg.min_height if cuts else 1, target - window)
        high = min(height - 1, target + window)
        if low >= high:
            cuts.append(min(max(target, low), height - 1))
            continue
        cuts.append(_quietest_row(ink, low, high, level))

    bounds = [0, *cuts, height]
    return [(top, bottom) for top, bottom in pairwise(bounds) if bottom > top]


def _encode_params(cfg: SlicingConfig) -> tuple[str, list[int]]:
    if cfg.format == "png":
        return ".png", [cv2.IMWRITE_PNG_COMPRESSION, 6]
    return ".jpg", [cv2.IMWRITE_JPEG_QUALITY, cfg.quality]


def _read(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"nao consegui decodificar a imagem {path}")
    return image


class _SliceWriter:
    """Numeracao continua das fatias, independente de qual arquivo as originou."""

    def __init__(self, destination: Path, cfg: SlicingConfig) -> None:
        self._destination = destination
        self._suffix, self._params = _encode_params(cfg)
        self._count = 0
        self.written: list[Path] = []
        destination.mkdir(parents=True, exist_ok=True)

    def write(self, band: np.ndarray) -> None:
        self._count += 1
        target = self._destination / f"p{self._count:04d}{self._suffix}"
        if not cv2.imwrite(str(target), band, self._params):
            raise ValueError(f"nao consegui escrever {target}")
        self.written.append(target)


def slice_stream(paths: Sequence[Path], destination: Path, cfg: SlicingConfig) -> list[Path]:
    """Fatia varias capturas como se fossem uma tira continua.

    Um macro de rolagem corta a captura num teto fixo de altura - 12000px na captura
    de teste - e esse corte e cego: medido ali, um balao terminava com o arco no fim
    de um arquivo e o texto no comeco do proximo, virando duas metades ilegiveis.

    Por isso o resto nao fatiado de um arquivo e carregado para o inicio do seguinte
    antes de procurar a proxima costura. O carry nunca passa de uma fatia, entao a
    memoria fica limitada ao arquivo atual.
    """
    writer = _SliceWriter(destination, cfg)
    carry: np.ndarray | None = None

    for position, path in enumerate(paths):
        image = _read(path)

        if carry is not None:
            if carry.shape[1] != image.shape[1]:
                # Largura diferente nao empilha; a tira anterior fecha aqui.
                writer.write(carry)
            else:
                image = np.vstack([carry, image])
            carry = None

        bounds = plan_cuts(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cfg)
        is_last_file = position == len(paths) - 1
        keep = bounds if is_last_file else bounds[:-1]

        for top, bottom in keep:
            writer.write(image[top:bottom])
        if not is_last_file:
            carry = image[bounds[-1][0] :].copy()

    if carry is not None:
        writer.write(carry)

    log.info(
        "operation=slice sources=%d slices=%d",
        len(paths), len(writer.written),
    )
    return writer.written


def slice_image(path: Path, destination: Path, cfg: SlicingConfig) -> list[Path]:
    """Fatia uma captura isolada."""
    return slice_stream([path], destination, cfg)


def slice_chapter_in_place(chapter_dir: Path, images: Sequence[Path], cfg: SlicingConfig) -> list[Path]:
    """Fatia as capturas altas do capitulo, guardando as originais em `_source/`.

    Idempotente: um capitulo ja fatiado nao tem mais imagem alta solta, entao
    rodar de novo nao faz nada.
    """
    tall: list[Path] = []
    for path in images:
        header = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if header is None:
            raise ValueError(f"nao consegui decodificar a imagem {path}")
        height, width = header.shape[:2]
        if is_tall(width, height, cfg):
            tall.append(path)

    if not tall:
        return []

    # Todas de uma vez, e nao uma a uma: as capturas de um capitulo sao partes de
    # uma tira so, e fatiar cada arquivo isoladamente manteria os baloes partidos
    # exatamente nas fronteiras entre eles.
    written = slice_stream(tall, chapter_dir, cfg)

    archive = chapter_dir / SOURCE_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    for path in tall:
        shutil.move(str(path), str(archive / path.name))

    return written
