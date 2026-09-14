"""Orquestracao: imagens -> extract.json -> chapter.<engine>.json.

As duas metades sao deliberadamente separaveis. A extracao e cara e agnostica de
motor; a traducao e barata e descartavel. Rodar `--engine free` depois de
`--engine claude` reaproveita o extract.json inteiro.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .detect import draw_boxes
from .detectors.base import Detector, create_detector
from .engines.base import TranslationEngine
from .models import (
    PIPELINE_VERSION,
    BBox,
    Chapter,
    Detection,
    ExtractedBlock,
    ExtractedPage,
    Extraction,
)
from .ocr import BlockReading, is_usable, read_block
from .ordering import reading_order
from .slicing import slice_chapter_in_place
from .store import (
    chapter_output_dir,
    image_sha256,
    is_page_current,
    list_page_images,
    load_extraction,
    load_glossary,
    save_chapter,
    save_extraction,
)

log = logging.getLogger("mangatl.pipeline")


class ChapterNotFoundError(FileNotFoundError):
    pass


@dataclass(frozen=True)
class ExtractionReport:
    extraction: Extraction
    reused_pages: int
    extracted_pages: int

    @property
    def block_count(self) -> int:
        return sum(len(page.blocks) for page in self.extraction.pages)


def chapter_input_dir(cfg: Config, series: str, chapter: str) -> Path:
    directory = cfg.library_dir / series / chapter
    if not directory.is_dir():
        raise ChapterNotFoundError(f"capitulo nao encontrado: {directory}")
    return directory


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"nao consegui decodificar a imagem {path}")
    return image


def _drop_repeated_readings(
    readings: list[tuple[Detection, BlockReading]],
) -> tuple[list[tuple[Detection, BlockReading]], list[BBox]]:
    """Remove o mesmo texto lido duas vezes na mesma pagina.

    Um balao dentro de um painel branco produz duas caixas: a do balao e a do
    painel, que o contem parcialmente. Medido numa pagina real, elas tinham IoU
    0.26 e sobreposicao de 0.52 da menor - abaixo dos dois criterios de fusao -
    e as duas liam a mesma fala, que aparecia duplicada no leitor.

    Exigir sobreposicao evita apagar repeticao legitima: duas falas iguais em
    baloes distintos ("...", "NAO!") nao se cruzam. Entre as duas, fica a caixa
    menor, que e a do balao e nao a do painel - e a que a Fase 2 precisa.
    """
    kept: list[tuple[Detection, BlockReading]] = []
    removed: list[BBox] = []

    for detection, reading in readings:
        twin = next(
            (
                index
                for index, (other, other_reading) in enumerate(kept)
                if other_reading.text == reading.text
                and other.bbox.intersection_area(detection.bbox) > 0
            ),
            None,
        )
        if twin is None:
            kept.append((detection, reading))
            continue
        if detection.bbox.area < kept[twin][0].bbox.area:
            removed.append(kept[twin][0].bbox)
            kept[twin] = (detection, reading)
        else:
            removed.append(detection.bbox)

    return kept, removed


def _extract_page(
    cfg: Config, detector: Detector, path: Path, index: int, *, debug_dir: Path | None
) -> ExtractedPage:
    image = _read_image(path)
    height, width = image.shape[:2]

    detections = detector.detect(image)
    order = reading_order(
        [detection.bbox for detection in detections],
        band_overlap=cfg.reading_order.band_overlap,
        rtl=cfg.reading_order.rtl,
    )
    ordered = [detections[position] for position in order]

    readings: list[tuple[Detection, BlockReading]] = []
    dropped: list[BBox] = []
    for detection in ordered:
        # O recorte do texto, nao o do balao: o contorno que sobra em volta faz o
        # Tesseract ler a borda como glifo.
        reading = read_block(image, detection.text_bbox, cfg.ocr)
        if not is_usable(reading.text, reading.confidence, cfg.ocr):
            dropped.append(detection.bbox)
            continue
        readings.append((detection, reading))

    readings, duplicates = _drop_repeated_readings(readings)
    dropped.extend(duplicates)

    kept = [detection for detection, _ in readings]
    blocks = [
        ExtractedBlock(
            id=f"p{index:03d}-b{position:02d}",
            bbox=detection.bbox,
            raw_text=reading.text,
            confidence=reading.confidence,
            kind=detection.kind,
            text_bbox=reading.text_bbox,
            source_font_px=reading.source_font_px,
        )
        for position, (detection, reading) in enumerate(readings, start=1)
    ]

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{path.stem}.png"), draw_boxes(image, kept, dropped))

    return ExtractedPage(
        index=index,
        image=path.name,
        width=width,
        height=height,
        image_sha256=image_sha256(path),
        blocks=tuple(blocks),
    )


def extract_chapter(
    cfg: Config,
    series: str,
    chapter: str,
    *,
    force: bool = False,
    debug_boxes: bool = False,
    detector_name: str | None = None,
) -> ExtractionReport:
    """Detecta e OCRa as paginas do capitulo, pulando as que nao mudaram."""
    chapter_dir = chapter_input_dir(cfg, series, chapter)
    images = list_page_images(chapter_dir)
    if not images:
        raise ChapterNotFoundError(f"nenhuma imagem em {chapter_dir}")

    # Captura de rolagem vira paginas normais antes de qualquer outra coisa, para
    # que o resto do pipeline nunca precise saber que ela existiu.
    if slice_chapter_in_place(chapter_dir, images, cfg.slicing):
        images = list_page_images(chapter_dir)

    previous = None if force else load_extraction(cfg, series, chapter)
    debug_dir = chapter_output_dir(cfg, series, chapter) / "debug" if debug_boxes else None

    # Um detector por capitulo, nao por pagina: o backend treinado leva segundos
    # para carregar o modelo, e sao 155 paginas.
    detector = create_detector(detector_name or cfg.detect.backend, cfg)
    log.info("operation=extract_chapter detector=%s pages=%d", detector.name, len(images))

    pages: list[ExtractedPage] = []
    reused = 0
    for index, path in enumerate(images, start=1):
        cached = previous.page_by_image(path.name) if previous else None
        if cached is not None and is_page_current(previous, path, PIPELINE_VERSION) and not debug_boxes:
            pages.append(cached.model_copy(update={"index": index}))
            reused += 1
            continue
        log.info("operation=extract_page page=%d image=%s", index, path.name)
        pages.append(_extract_page(cfg, detector, path, index, debug_dir=debug_dir))

    extraction = Extraction(
        series=series,
        chapter=chapter,
        pipeline_version=PIPELINE_VERSION,
        pages=tuple(pages),
    )
    save_extraction(cfg, extraction)

    return ExtractionReport(
        extraction=extraction,
        reused_pages=reused,
        extracted_pages=len(pages) - reused,
    )


def translate_chapter(cfg: Config, extraction: Extraction, engine: TranslationEngine) -> Chapter:
    chapter_dir = chapter_input_dir(cfg, extraction.series, extraction.chapter)
    glossary = load_glossary(cfg, extraction.series)

    pages = engine.translate_chapter(extraction.pages, glossary, chapter_dir)

    chapter = Chapter(
        series=extraction.series,
        chapter=extraction.chapter,
        engine=engine.name,
        model=engine.model,
        pipeline_version=PIPELINE_VERSION,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pages=tuple(pages),
    )
    save_chapter(cfg, chapter)
    return chapter


def translated_line_count(pages: Sequence[object]) -> int:
    return sum(len(page.blocks) for page in pages)
