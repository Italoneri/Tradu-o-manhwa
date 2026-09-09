"""Interface dos motores de traducao.

Um motor recebe paginas ja extraidas e devolve paginas traduzidas. Nada mais.
Ele nao le disco, nao conhece config de OCR e nao decide ordem de leitura - por
isso trocar `--engine` nao toca em nenhuma outra parte do pipeline.

Os motores tem capacidades diferentes e isso e explicito em `sees_images`:
`claude` recebe a imagem da pagina e consegue corrigir OCR embaralhado; `free`
so recebe texto e nao tem como perceber que o OCR saiu errado.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from ..config import Config
from ..models import ExtractedPage, TranslatedPage


class TranslationEngine(Protocol):
    name: str
    model: str | None
    sees_images: bool

    def translate_chapter(
        self,
        pages: Sequence[ExtractedPage],
        glossary: Mapping[str, str],
        chapter_dir: object,
    ) -> Sequence[TranslatedPage]: ...


class UnknownEngineError(ValueError):
    pass


class TranslationError(RuntimeError):
    """Falha de traducao com contexto suficiente para saber onde parou."""


def _make_claude(cfg: Config) -> TranslationEngine:
    from .claude import ClaudeEngine

    return ClaudeEngine(cfg)


def _make_argos(cfg: Config) -> TranslationEngine:
    from .argos import ArgosEngine

    return ArgosEngine(cfg)


_FACTORIES: Mapping[str, Callable[[Config], TranslationEngine]] = {
    "claude": _make_claude,
    "free": _make_argos,
}
"""Import tardio de proposito: um motor com dependencia faltando nao derruba o outro."""


def available_engines() -> list[str]:
    return sorted(_FACTORIES)


def create_engine(name: str, cfg: Config) -> TranslationEngine:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise UnknownEngineError(f"motor '{name}' nao existe; disponiveis: {', '.join(available_engines())}")
    return factory(cfg)
