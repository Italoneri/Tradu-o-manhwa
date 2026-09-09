"""Motor `free`: Argos Translate rodando local, sem custo e sem rede.

Limites reais deste motor, ditos em voz alta em vez de escondidos:

- Nao ve a imagem. Se o OCR saiu embaralhado, ele traduz o embaralhado.
- E um modelo MT pequeno: traduz literal, erra idiom e giria.
- Nao aceita instrucao de tom. Nao ha como pedir "fala de HQ".

O glossario, que no motor `claude` e uma instrucao, aqui vira protecao por
marcador: o termo sai do texto antes da traducao e volta depois, para o modelo
nao ter chance de reescrever nome proprio.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..config import Config
from ..models import ExtractedPage, TranslatedBlock, TranslatedPage
from .base import TranslationError

log = logging.getLogger("mangatl.argos")

TERM_MARKER = "MTLTERM{index}X"
"""Marcador em caixa alta e sem espaco: o que os modelos MT mais tendem a copiar intacto."""

_MARKER_PATTERN = re.compile(r"MTLTERM(\d+)X")


def protect_terms(text: str, glossary: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    """Troca termos do glossario por marcadores, devolvendo o mapa para restaurar depois."""
    if not glossary:
        return text, {}

    replacements: dict[str, str] = {}
    protected = text
    # Termos longos primeiro para "Sect Master" nao ser quebrado por "Master".
    for index, source in enumerate(sorted(glossary, key=len, reverse=True)):
        pattern = re.compile(rf"\b{re.escape(source)}\b", re.IGNORECASE)
        if not pattern.search(protected):
            continue
        marker = TERM_MARKER.format(index=index)
        protected = pattern.sub(marker, protected)
        replacements[marker] = glossary[source]
    return protected, replacements


def restore_terms(text: str, replacements: Mapping[str, str]) -> str:
    if not replacements:
        return text
    return _MARKER_PATTERN.sub(lambda match: replacements.get(match.group(0), match.group(0)), text)


def install_language_package(source_lang: str, target_lang: str) -> str:
    """Baixa e instala o par de idiomas. Roda uma vez, via `mangatl setup-free`."""
    import argostranslate.package

    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    match = next(
        (
            package
            for package in available
            if package.from_code == source_lang and package.to_code == target_lang
        ),
        None,
    )
    if match is None:
        raise TranslationError(f"Argos nao publica o par {source_lang}->{target_lang}")

    argostranslate.package.install_from_path(match.download())
    return f"{match.from_name} -> {match.to_name}"


def language_package_installed(source_lang: str, target_lang: str) -> bool:
    try:
        import argostranslate.translate
    except ImportError:
        return False

    installed = argostranslate.translate.get_installed_languages()
    source = next((lang for lang in installed if lang.code == source_lang), None)
    target = next((lang for lang in installed if lang.code == target_lang), None)
    if source is None or target is None:
        return False
    try:
        return source.get_translation(target) is not None
    except Exception:  # noqa: BLE001 - a API do Argos varia entre versoes
        return False


class ArgosEngine:
    name = "free"
    model = None
    sees_images = False

    def __init__(self, cfg: Config) -> None:
        self._source = cfg.translation.source_lang
        self._target = cfg.translation.target_lang
        try:
            import argostranslate.translate
        except ImportError as error:
            raise TranslationError(
                "argostranslate nao instalado. Rode: pip install -e '.[free]' && mangatl setup-free"
            ) from error
        self._translate_text = argostranslate.translate.translate

        if not language_package_installed(self._source, self._target):
            raise TranslationError(
                f"pacote de idioma {self._source}->{self._target} ausente. Rode: mangatl setup-free"
            )

    def translate_chapter(
        self,
        pages: Sequence[ExtractedPage],
        glossary: Mapping[str, str],
        chapter_dir: Path,
    ) -> list[TranslatedPage]:
        log.info(
            "operation=translate_chapter engine=free pages=%d sees_images=False",
            len(pages),
        )
        return [self._translate_page(page, glossary) for page in pages]

    def _translate_page(self, page: ExtractedPage, glossary: Mapping[str, str]) -> TranslatedPage:
        blocks = []
        for block in page.blocks:
            if not block.raw_text.strip():
                continue
            protected, replacements = protect_terms(block.raw_text, glossary)
            translated = restore_terms(
                self._translate_text(protected, self._source, self._target), replacements
            )
            blocks.append(
                TranslatedBlock(
                    id=block.id,
                    bbox=block.bbox,
                    source_text=block.raw_text,
                    text=translated.strip(),
                )
            )
        return TranslatedPage(
            index=page.index,
            image=page.image,
            width=page.width,
            height=page.height,
            blocks=tuple(blocks),
        )
