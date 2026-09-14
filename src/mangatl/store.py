"""Leitura e escrita dos artefatos em disco.

Dois arquivos por capitulo, deliberadamente separados:

    extract.json          OCR cru, agnostico de motor, caro de produzir
    chapter.<engine>.json saida de um motor, barata, descartavel

Trocar `--engine` reescreve so o segundo. E o que faz o OCR nao rodar de novo.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .models import (
    Chapter,
    ChapterEntry,
    Extraction,
    Library,
    SeriesEntry,
)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
EXTRACTION_FILENAME = "extract.json"
COVER_STEM = "cover"
"""Nome reservado da capa: `cover.jpg`, `cover.png`, `cover.webp`.

Nome fixo e formato livre. Uma pasta de capitulo so tem paginas e essa capa,
entao a alternativa - "toda imagem fora do padrao de nome e capa" - nao consegue
escolher entre duas imagens soltas, e escolheria errado em silencio.
"""
LIBRARY_FILENAME = "library.json"
_DIGITS = re.compile(r"(\d+)")


def _natural_key(name: str) -> tuple[object, ...]:
    """Ordena 2.jpg antes de 10.jpg, ao contrario da ordem lexicografica."""
    return tuple(int(part) if part.isdigit() else part.lower() for part in _DIGITS.split(name))


def _is_image(entry: Path) -> bool:
    return entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES


def _is_cover(entry: Path) -> bool:
    return entry.stem.lower() == COVER_STEM


def list_page_images(chapter_dir: Path) -> list[Path]:
    """As paginas do capitulo, sem a capa.

    Sem esse filtro a capa vira pagina: entra no OCR, na traducao e no meio da leitura.
    """
    return sorted(
        (entry for entry in chapter_dir.iterdir() if _is_image(entry) and not _is_cover(entry)),
        key=lambda entry: _natural_key(entry.name),
    )


def find_cover(directory: Path) -> Path | None:
    """A capa da pasta, em qualquer formato de imagem. None quando nao ha."""
    if not directory.is_dir():
        return None
    covers = sorted(
        (entry for entry in directory.iterdir() if _is_cover(entry) and _is_image(entry)),
        key=lambda entry: _natural_key(entry.name),
    )
    return covers[0] if covers else None


def image_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chapter_output_dir(cfg: Config, series: str, chapter: str) -> Path:
    return cfg.output_dir / series / chapter


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_extraction(cfg: Config, series: str, chapter: str) -> Extraction | None:
    path = chapter_output_dir(cfg, series, chapter) / EXTRACTION_FILENAME
    if not path.is_file():
        return None
    return Extraction.model_validate_json(path.read_text(encoding="utf-8"))


def save_extraction(cfg: Config, extraction: Extraction) -> Path:
    path = chapter_output_dir(cfg, extraction.series, extraction.chapter) / EXTRACTION_FILENAME
    return _write_json(path, extraction.model_dump(mode="json"))


def chapter_filename(engine: str) -> str:
    return f"chapter.{engine}.json"


def load_chapter(cfg: Config, series: str, chapter: str, engine: str) -> Chapter | None:
    path = chapter_output_dir(cfg, series, chapter) / chapter_filename(engine)
    if not path.is_file():
        return None
    return Chapter.model_validate_json(path.read_text(encoding="utf-8"))


def save_chapter(cfg: Config, chapter: Chapter) -> Path:
    path = chapter_output_dir(cfg, chapter.series, chapter.chapter) / chapter_filename(chapter.engine)
    return _write_json(path, chapter.model_dump(mode="json"))


def load_glossary(cfg: Config, series: str) -> dict[str, str]:
    """Termos e nomes proprios fixos da serie, para o motor manter consistencia entre capitulos."""
    path = cfg.library_dir / series / "glossary.json"
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} deve conter um objeto JSON de termo -> traducao")
    return {str(key): str(value) for key, value in loaded.items()}


def is_page_current(extraction: Extraction | None, image: Path, pipeline_version: int) -> bool:
    """True quando a pagina ja foi extraida por esta versao do pipeline e nao mudou."""
    if extraction is None or extraction.pipeline_version != pipeline_version:
        return False
    page = extraction.page_by_image(image.name)
    return page is not None and page.image_sha256 == image_sha256(image)


def discover_chapters(cfg: Config) -> Iterable[tuple[str, str]]:
    """Pares (serie, capitulo) presentes na biblioteca de entrada."""
    if not cfg.library_dir.is_dir():
        return []
    pairs: list[tuple[str, str]] = []
    for series_dir in sorted(cfg.library_dir.iterdir(), key=lambda p: _natural_key(p.name)):
        if not series_dir.is_dir():
            continue
        for chapter_dir in sorted(series_dir.iterdir(), key=lambda p: _natural_key(p.name)):
            if chapter_dir.is_dir() and list_page_images(chapter_dir):
                pairs.append((series_dir.name, chapter_dir.name))
    return pairs


def _cover_url(cfg: Config, directory: Path) -> str | None:
    """A capa da pasta como caminho relativo a raiz servida, que e o que o leitor busca."""
    cover = find_cover(directory)
    if cover is None:
        return None
    return "/".join((cfg.paths.library, *cover.relative_to(cfg.library_dir).parts))


def _translated_engines(cfg: Config, series: str, chapter: str) -> tuple[str, ...]:
    output_dir = chapter_output_dir(cfg, series, chapter)
    return tuple(
        sorted(path.name.removeprefix("chapter.").removesuffix(".json") for path in output_dir.glob("chapter.*.json"))
    )


def _series_cover(cfg: Config, series: str, chapters: Iterable[ChapterEntry]) -> str | None:
    """A capa propria da serie tem precedencia; sem ela, a do primeiro capitulo que tiver."""
    own = _cover_url(cfg, cfg.library_dir / series)
    if own is not None:
        return own
    return next((entry.cover for entry in chapters if entry.cover is not None), None)


def build_library(cfg: Config) -> Library:
    """Indice do que ja foi processado, com os motores e as capas por capitulo."""
    chapters_by_series: dict[str, list[ChapterEntry]] = defaultdict(list)
    for series, chapter in discover_chapters(cfg):
        engines = _translated_engines(cfg, series, chapter)
        if not engines:
            continue

        first = load_chapter(cfg, series, chapter, engines[0])
        chapters_by_series[series].append(
            ChapterEntry(
                chapter=chapter,
                page_count=len(first.pages) if first else 0,
                engines=engines,
                cover=_cover_url(cfg, cfg.library_dir / series / chapter),
            )
        )

    return Library(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        library_base=cfg.paths.library,
        output_base=cfg.paths.output,
        series=tuple(
            SeriesEntry(series=series, chapters=tuple(chapters), cover=_series_cover(cfg, series, chapters))
            for series, chapters in chapters_by_series.items()
        ),
    )


def save_library(cfg: Config, library: Library) -> Path:
    return _write_json(cfg.output_dir / LIBRARY_FILENAME, library.model_dump(mode="json"))
