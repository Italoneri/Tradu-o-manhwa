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
from .detect import detect_bubbles, draw_boxes
from .engines.base import TranslationEngine
from .models import (
    PIPELINE_VERSION,
    Chapter,
    ExtractedBlock,
    ExtractedPage,
    Extraction,
)
from .ocr import is_usable, read_block
from .ordering import reading_order
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


def _extract_page(
    cfg: Config, path: Path, index: int, *, debug_dir: Path | None
) -> ExtractedPage:
    image = _read_image(path)
    height, width = image.shape[:2]

    boxes = detect_bubbles(image, cfg.detect)
    order = reading_order(
        boxes,
        band_overlap=cfg.reading_order.band_overlap,
        rtl=cfg.reading_order.rtl,
    )
    ordered = [boxes[position] for position in order]

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{path.stem}.png"), draw_boxes(image, ordered))

    blocks: list[ExtractedBlock] = []
    for position, box in enumerate(ordered, start=1):
        text, confidence = read_block(image, box, cfg.ocr)
        if not is_usable(text, confidence, cfg.ocr):
            continue
        blocks.append(
            ExtractedBlock(
                id=f"p{index:03d}-b{position:02d}",
                bbox=box,
                raw_text=text,
                confidence=confidence,
            )
        )

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
) -> ExtractionReport:
    """Detecta e OCRa as paginas do capitulo, pulando as que nao mudaram."""
    chapter_dir = chapter_input_dir(cfg, series, chapter)
    images = list_page_images(chapter_dir)
    if not images:
        raise ChapterNotFoundError(f"nenhuma imagem em {chapter_dir}")

    previous = None if force else load_extraction(cfg, series, chapter)
    debug_dir = chapter_output_dir(cfg, series, chapter) / "debug" if debug_boxes else None

    pages: list[ExtractedPage] = []
    reused = 0
    for index, path in enumerate(images, start=1):
        cached = previous.page_by_image(path.name) if previous else None
        if cached is not None and is_page_current(previous, path, PIPELINE_VERSION) and not debug_boxes:
            pages.append(cached.model_copy(update={"index": index}))
            reused += 1
            continue
        log.info("operation=extract_page page=%d image=%s", index, path.name)
        pages.append(_extract_page(cfg, path, index, debug_dir=debug_dir))

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
