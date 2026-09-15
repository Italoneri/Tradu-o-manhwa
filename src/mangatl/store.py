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
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .models import (
    Chapter,
    ChapterEntry,
    ChapterState,
    Extraction,
    Library,
    SeriesEntry,
    SeriesMeta,
    SeriesState,
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
SERIES_FILENAME = "series.json"
GLOSSARY_FILENAME = "glossary.json"
SOURCE_DIRNAME = "_source"
"""Onde a captura original vai parar depois de fatiada.

Mora aqui e nao no `slicing.py` porque e fato de layout de disco, e quem caminha a
biblioteca precisa saber que essa pasta nao e capitulo. O caminho contrario -
store importando slicing - arrastaria cv2 para dentro de quem so le diretorio."""

INCOMING_SUFFIX = ".incoming"
"""Sufixo da area de espera do upload.

Mora aqui porque quem caminha o disco precisa ignorar essas pastas e quem as cria
precisa escrever o mesmo nome. Duas constantes seria uma divergindo da outra."""
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
    path = cfg.library_dir / series / GLOSSARY_FILENAME
    if not path.is_file():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} deve conter um objeto JSON de termo -> traducao")
    return {str(key): str(value) for key, value in loaded.items()}


def save_glossary(cfg: Config, series: str, terms: Mapping[str, str]) -> Path:
    return _write_json(cfg.library_dir / series / GLOSSARY_FILENAME, dict(terms))


def load_series_meta(cfg: Config, series: str) -> SeriesMeta:
    """Titulo e status da serie, com os defaults quando o arquivo nao existe.

    Tolerante no molde de `load_glossary`: ausente devolve defaults, e o titulo
    vazio quer dizer "use o slug". Quem escreve o arquivo pelo painel ja validou
    antes; quem escreveu na mao nao pode derrubar a biblioteca por um typo.
    """
    path = cfg.library_dir / series / SERIES_FILENAME
    if not path.is_file():
        return SeriesMeta(title=series)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} deve conter um objeto JSON")
    meta = SeriesMeta.model_validate(loaded)
    return meta if meta.title else meta.model_copy(update={"title": series})


def save_series_meta(cfg: Config, series: str, meta: SeriesMeta) -> Path:
    return _write_json(cfg.library_dir / series / SERIES_FILENAME, meta.model_dump(mode="json"))


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


def _series_cover(
    cfg: Config, series: str, chapter_covers: Iterable[str | None], declared: str | None
) -> str | None:
    """A capa da serie, da mais explicita para a mais adivinhada.

    O `series.json` vem primeiro porque e a unica das tres que alguem escreveu de
    proposito; so vale se o arquivo existir mesmo, senao um nome errado ali
    apagaria a capa que ja funcionava.
    """
    if declared and (cfg.library_dir / series / declared).is_file():
        return f"{cfg.paths.library}/{series}/{declared}"
    own = _cover_url(cfg, cfg.library_dir / series)
    if own is not None:
        return own
    return next((cover for cover in chapter_covers if cover is not None), None)


def _chapter_names(series_dir: Path) -> tuple[list[str], set[str]]:
    """Os capitulos da serie e quais deles tem area de espera aberta.

    Um capitulo que so existe como `<cap>.incoming/` entra na lista mesmo assim: e
    upload interrompido, e so aparecendo e que a tela pode oferecer continuar.
    """
    committed: set[str] = set()
    incoming: set[str] = set()
    for entry in series_dir.iterdir():
        if not entry.is_dir() or entry.name == SOURCE_DIRNAME:
            continue
        if entry.name.endswith(INCOMING_SUFFIX):
            incoming.add(entry.name.removesuffix(INCOMING_SUFFIX))
        else:
            committed.add(entry.name)
    return sorted(committed | incoming, key=_natural_key), incoming


def discover_series(cfg: Config) -> tuple[SeriesState, ...]:
    """Toda serie em library/, com todo capitulo, traduzido ou nao.

    Percorre `library_dir` direto em vez de passar por `discover_chapters`, que so
    devolve par (serie, capitulo) e por isso nao tem como representar serie sem
    capitulo nenhum - que e o primeiro estado de toda serie criada pelo painel.
    """
    if not cfg.library_dir.is_dir():
        return ()

    states: list[SeriesState] = []
    for series_dir in sorted(cfg.library_dir.iterdir(), key=lambda p: _natural_key(p.name)):
        if not series_dir.is_dir():
            continue

        names, incoming = _chapter_names(series_dir)
        chapters = tuple(
            ChapterState(
                chapter=name,
                image_count=len(list_page_images(series_dir / name))
                if (series_dir / name).is_dir()
                else 0,
                engines=_translated_engines(cfg, series_dir.name, name),
                incoming=name in incoming,
            )
            for name in names
        )
        meta = load_series_meta(cfg, series_dir.name)
        states.append(
            SeriesState(
                series=series_dir.name,
                title=meta.title,
                cover=_series_cover(
                    cfg,
                    series_dir.name,
                    (_cover_url(cfg, series_dir / name) for name in names),
                    meta.cover,
                ),
                chapters=chapters,
            )
        )
    return tuple(states)


def _readable_chapters(cfg: Config, state: SeriesState) -> list[ChapterEntry]:
    """Os capitulos do estado que o leitor consegue abrir.

    `page_count` sai do JSON traduzido e nao do `image_count`: aquele conta as
    paginas do capitulo processado, este conta arquivos no disco, e um capitulo de
    tres capturas de rolagem tem 3 arquivos e 155 paginas.
    """
    entries = []
    for chapter in state.chapters:
        if not chapter.engines:
            continue
        first = load_chapter(cfg, state.series, chapter.chapter, chapter.engines[0])
        entries.append(
            ChapterEntry(
                chapter=chapter.chapter,
                page_count=len(first.pages) if first else 0,
                engines=chapter.engines,
                cover=_cover_url(cfg, cfg.library_dir / state.series / chapter.chapter),
            )
        )
    return entries


def build_library(cfg: Config) -> Library:
    """Indice do que ja foi processado, com os motores e as capas por capitulo.

    Consome `discover_series` e filtra: capitulo sem motor nao tem o que abrir, e
    serie sem nenhum capitulo legivel nao entra no indice. A caminhada do disco e
    uma so; as duas funcoes respondem perguntas diferentes sobre ela - o painel
    pergunta "o que existe" e o leitor pergunta "o que da para ler".

    A capa e recalculada sobre os capitulos filtrados, e nao herdada do
    `SeriesState`: a heranca vale "o primeiro capitulo que tiver capa", e o
    primeiro do disco pode ser um que o leitor nem lista.
    """
    series: list[SeriesEntry] = []
    for state in discover_series(cfg):
        entries = _readable_chapters(cfg, state)
        if not entries:
            continue

        declared = load_series_meta(cfg, state.series).cover
        series.append(
            SeriesEntry(
                series=state.series,
                title=state.title,
                chapters=tuple(entries),
                cover=_series_cover(cfg, state.series, (e.cover for e in entries), declared),
            )
        )

    return Library(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        library_base=cfg.paths.library,
        output_base=cfg.paths.output,
        series=tuple(series),
    )


def save_library(cfg: Config, library: Library) -> Path:
    return _write_json(cfg.output_dir / LIBRARY_FILENAME, library.model_dump(mode="json"))
