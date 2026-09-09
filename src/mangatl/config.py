"""Carga e validacao do config.toml.

Parse na borda, confia depois: todo o resto do pipeline recebe um `Config`
validado e nunca toca em dicionario cru nem em variavel de ambiente.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CONFIG_FILENAME = "config.toml"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PathsConfig(Frozen):
    library: str = "library"
    output: str = "output"


class TranslationConfig(Frozen):
    engine: str = "claude"
    model: str = "claude-sonnet-5"
    source_lang: str = "en"
    target_lang: str = "pt"
    chunk_pages: int = Field(default=12, ge=1, le=40)
    max_image_side: int = Field(default=1568, ge=256)
    timeout_seconds: float = Field(default=600.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    effort: str = "medium"


class ModelPricing(Frozen):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(default=0.0, ge=0)


class OcrConfig(Frozen):
    lang: str = "eng"
    psm: int = Field(default=6, ge=0, le=13)
    upscale: int = Field(default=3, ge=1, le=8)
    padding: int = Field(default=4, ge=0)
    min_confidence: float = Field(default=45.0, ge=0, le=100)
    min_chars: int = Field(default=3, ge=1)


class DetectConfig(Frozen):
    min_area_ratio: float = Field(default=0.002, gt=0, lt=1)
    max_area_ratio: float = Field(default=0.25, gt=0, le=1)
    min_aspect: float = Field(default=0.15, gt=0)
    max_aspect: float = Field(default=8.0, gt=0)
    min_fill_ratio: float = Field(default=0.55, ge=0, le=1)
    min_interior_brightness: float = Field(default=200.0, ge=0, le=255)
    min_ink_ratio: float = Field(default=0.02, ge=0, le=1)
    max_ink_ratio: float = Field(default=0.40, ge=0, le=1)
    merge_iou: float = Field(default=0.30, ge=0, le=1)


class ReadingOrderConfig(Frozen):
    rtl: bool = False
    band_overlap: float = Field(default=0.40, gt=0, le=1)


class Config(Frozen):
    root: Path
    paths: PathsConfig = PathsConfig()
    translation: TranslationConfig = TranslationConfig()
    pricing: dict[str, ModelPricing] = {}
    ocr: OcrConfig = OcrConfig()
    detect: DetectConfig = DetectConfig()
    reading_order: ReadingOrderConfig = ReadingOrderConfig()

    @property
    def library_dir(self) -> Path:
        return self.root / self.paths.library

    @property
    def output_dir(self) -> Path:
        return self.root / self.paths.output

    def pricing_for(self, model: str) -> ModelPricing | None:
        return self.pricing.get(model)


def find_project_root(start: Path | None = None) -> Path:
    """Sobe a partir de `start` ate achar o diretorio que contem config.toml."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
    raise FileNotFoundError(
        f"{CONFIG_FILENAME} nao encontrado a partir de {current}. "
        "Rode o mangatl de dentro do projeto."
    )


def load_config(start: Path | None = None) -> Config:
    root = find_project_root(start)
    with (root / CONFIG_FILENAME).open("rb") as handle:
        raw = tomllib.load(handle)
    return Config(root=root, **raw)
