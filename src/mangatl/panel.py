"""Painel: o que so a propria maquina pode fazer com a biblioteca.

O leitor escuta em 0.0.0.0 porque o celular precisa alcanca-lo. O painel escreve
em disco e dispara o pipeline, entao toda rota sob `/api/` responde 403 para quem
nao vem de 127.0.0.1.

Isso nao e autenticacao e nao finge ser: quem tem shell nesta maquina ja tinha
tudo. O que a checagem impede e o vizinho de Wi-Fi - estar na mesma LAN nao e
credencial nenhuma, e o disco que este modulo escreve e o mesmo que guarda o .env.

Este modulo depende do venv inteiro: pydantic, o store, o pipeline. O
`scripts/serve.py` roda no python do Windows, que nao tem venv, e importa so o
`serving.py`. Manter essa fronteira e o que impede um import pesado de derrubar o
servidor que o celular usa - e o Smart App Control do Windows e justamente o
motivo de aquele processo existir.

Ponto de extensao anotado e nao implementado: baixar capitulo de URL pediria uma
`import_from_url(url) -> list[Path]`. Qualquer site serio precisa de navegador
headless e quebra a cada mudanca de layout, entao a origem das imagens e upload.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Callable, Sequence
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import unquote

from .config import Config
from .engines.base import available_engines
from .jobs import Busy, JobRegistry
from .models import SeriesMeta
from .serving import ReaderHandler, serve_handler
from .store import (
    COVER_STEM,
    IMAGE_SUFFIXES,
    INCOMING_SUFFIX,
    _natural_key,
    discover_series,
    list_page_images,
    load_glossary,
    load_series_meta,
    save_glossary,
    save_series_meta,
)

API_PREFIX = "/api/"

LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

MAX_COMPONENT_CHARS = 120
"""Nome de pasta mais longo que isso e engano ou ataque; o NTFS para em 255 e o
caminho inteiro tambem conta."""

MAX_PAGE_BYTES = 25 * 1024 * 1024
"""Pagina de manhwa em jpeg fica em centenas de KB; 25MB ja cobre PNG sem perda
de uma captura de rolagem inteira."""

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
"""Um .cbz de capitulo longo passa raspando de 100MB."""

MAX_PAGES_PER_CHAPTER = 400
"""O capitulo medido aqui tem 155 fatias. Acima de 400 e pasta errada, nao capitulo."""

MAX_JSON_BYTES = 1024 * 1024
"""Corpo JSON do painel. Um glossario cheio nao passa de dezenas de KB; 1MB ja e
sinal de que veio coisa errada pelo cano."""

MAX_GLOSSARY_ENTRIES = 500
"""Acima disso nao e glossario de serie, e despejo de dicionario."""

MAX_ARCHIVE_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
"""Teto do descompactado, conferido somando `ZipInfo.file_size` ANTES de extrair.

Um zip de 1MB pode declarar 100GB. Somar o declarado nao e garantia contra zip que
mente, entao o laco de extracao tambem conta os bytes realmente escritos."""


def is_local_client(address: str) -> bool:
    """Se o pedido veio da propria maquina.

    O leitor escuta em 0.0.0.0 para o celular alcancar, e o painel escreve em disco
    e dispara o pipeline. Um `192.168.x` que chegue aqui e alguem do mesmo Wi-Fi,
    nao o dono - e a lista da LAN nao e credencial nenhuma.
    """
    return address in LOCAL_CLIENTS


# ---------- nomes vindos da rede ----------

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def safe_component(name: str) -> str | None:
    """Nome de serie ou capitulo aceitavel como nome de pasta, ou None.

    Recusa vazio, `.`, `..`, barra, barra invertida, dois-pontos e caractere de
    controle. Recusa nome que comece com ponto, que e como o `is_servable` marca o
    que nao sai na rede - uma serie chamada `.git` seria invisivel para o leitor.

    Acento e espaco passam: a serie que ja existe se chama "Eu me tornei a Neta
    Desprezada", e renomear a pasta quebraria todo caminho gravado nos JSONs.
    """
    if not name or len(name) > MAX_COMPONENT_CHARS:
        return None
    if name.startswith(".") or name in {".", ".."}:
        return None
    if any(char in name for char in "/\\:"):
        return None
    if _CONTROL_CHARS.search(name):
        return None
    return name


def safe_page_name(name: str) -> str | None:
    """Nome de arquivo de pagina: `safe_component` mais extensao de imagem."""
    if safe_component(name) is None:
        return None
    return name if PurePosixPath(name).suffix.lower() in IMAGE_SUFFIXES else None


def safe_archive_entries(names: Sequence[str]) -> list[tuple[int, str]] | None:
    """Posicao e nome-base de cada entrada que pode ser extraida, ou None.

    Devolve a posicao junto porque quem extrai precisa voltar ao `ZipInfo`
    correspondente: gravar pelo nome-base e o ponto, e o nome-base sozinho nao
    diz de qual entrada ele veio.
    """
    kept: list[tuple[int, str]] = []
    for index, raw in enumerate(names):
        name = raw.replace("\\", "/")
        if name.startswith("/") or ":" in name:
            return None

        parts = PurePosixPath(name).parts
        if any(part in {".", ".."} for part in parts):
            return None
        if not parts or name.endswith("/"):
            continue
        if parts[0] == "__MACOSX":
            continue

        page = safe_page_name(parts[-1])
        if page is not None:
            kept.append((index, page))
    return kept


def safe_archive_members(names: Sequence[str]) -> list[str] | None:
    """Entradas de um zip que podem ser extraidas, ou None se alguma for hostil.

    Zip carrega caminho dentro de si e a stdlib extrai o que estiver escrito -
    inclusive `../../.env`. Uma entrada hostil condena o arquivo inteiro em vez de
    ser pulada: zip com caminho de fuga nao e capitulo mal montado, e continuar
    extraindo o resto seria tratar ataque como tropeco.

    Diretorio e ignorado, assim como o `__MACOSX/` que o Finder enfia em todo zip.
    Entrada que nao e imagem tambem e ignorada - nao e hostil, so nao e pagina.
    """
    entries = safe_archive_entries(names)
    return None if entries is None else [name for _, name in entries]


# ---------- o que chega no corpo ----------


class Invalid(ValueError):
    """Pedido malformado.

    Existe para o handler poder desistir numa linha e ainda assim virar 422 com a
    mensagem legivel, em vez de 500 com stack trace - erro de quem chamou nao e
    defeito de quem atende.
    """


def json_body(body: bytes) -> object:
    if len(body) > MAX_JSON_BYTES:
        raise Invalid(f"corpo de {len(body)} bytes; o teto e {MAX_JSON_BYTES}")
    try:
        return json.loads(body or b"null")
    except ValueError as error:
        raise Invalid(f"corpo nao e JSON valido: {error}") from error


def validate_glossary(payload: object) -> dict[str, str]:
    """O glossario como o pipeline espera, ou `Invalid` com o que esta errado.

    Validar na borda e o que impede o arquivo de virar um campo minado: quem le
    depois e o motor de traducao, no meio de um capitulo, onde um valor que nao e
    string vira erro sem contexto nenhum.
    """
    if not isinstance(payload, dict):
        raise Invalid("o glossario e um objeto JSON de termo -> traducao")
    if len(payload) > MAX_GLOSSARY_ENTRIES:
        raise Invalid(f"{len(payload)} termos; o teto e {MAX_GLOSSARY_ENTRIES}")

    terms: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            raise Invalid("todo termo precisa ser texto e nao pode ser vazio")
        if not isinstance(value, str):
            raise Invalid(f"a traducao de {key!r} precisa ser texto")
        terms[key] = value
    return terms


_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"BM", ".bmp"),
)


def image_suffix(data: bytes) -> str | None:
    """Extensao deduzida dos bytes, ou None se nao for imagem que o leitor serve.

    Dos bytes e nao do `Content-Type`: o cabecalho e do cliente, e gravar
    `cover.jpg` com um executavel dentro seria acreditar nele.
    """
    for magic, suffix in _IMAGE_MAGIC:
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


# ---------- roteador ----------

class Context(NamedTuple):
    """O que uma rota precisa alem do proprio pedido.

    O registro de jobs entra aqui e nao num modulo: ele guarda estado vivo, e
    estado vivo em variavel de modulo vaza entre servidores - inclusive entre dois
    testes que sobem o handler na mesma sessao.
    """

    cfg: Config
    jobs: JobRegistry


Handler = Callable[[Context, tuple[str, ...], bytes], tuple[int, object]]


class Route(NamedTuple):
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    max_body: int = MAX_JSON_BYTES
    """Teto do corpo, conferido pelo `Content-Length` antes de ler um byte."""

    body_required: bool = False
    """Se a rota recusa pedido sem `Content-Length` declarado.

    Marcado rota a rota, e nao deduzido do metodo: `commit` e o descarte da area
    de espera sao POST e DELETE sem corpo nenhum, e criar capitulo aceita corpo
    vazio para pedir a sugestao de numero. Cliente nenhum concorda sobre mandar
    `Content-Length: 0` num pedido sem corpo, entao exigir pelo metodo devolveria
    411 no uso normal.

    O default e o lado seguro de errar: uma rota que le corpo e esquece a marca
    recebe b"" e recusa com 422 pela propria validacao, em vez de aceitar lixo.
    """


class RouteMatch(NamedTuple):
    """Resultado do casamento de rota.

    `route` None significa caminho certo e metodo errado, que e 405 e nao 404:
    dizer "nao existe" para `POST /api/health` esconderia o erro de quem chamou.
    """

    route: Route | None
    groups: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


def _health(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    return HTTPStatus.OK, {
        "ok": True,
        "root": str(ctx.cfg.root),
        "engines": available_engines(),
        "detector": ctx.cfg.detect.backend,
        # Booleano, nunca o valor: a chave nao sai desta maquina por resposta nenhuma.
        "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "python": sys.version.split()[0],
    }


def _series(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Tudo que existe em library/, traduzido ou nao.

    `build_library` responderia outra pergunta - "o que da para ler" - e apagaria
    os dois estados que o painel existe para mostrar: a serie recem-criada e o
    capitulo enviado e ainda nao traduzido, que e o estado normal entre o upload e
    o botao de traduzir.
    """
    return HTTPStatus.OK, {
        "series": [state.model_dump(mode="json") for state in discover_series(ctx.cfg)]
    }


def _series_dir(cfg: Config, slug: str, *, must_exist: bool = True) -> Path:
    name = safe_component(slug)
    if name is None:
        raise Invalid(f"nome de serie inaceitavel: {slug!r}")
    directory = cfg.library_dir / name
    if must_exist and not directory.is_dir():
        raise Invalid(f"serie {name!r} nao existe")
    return directory


def _create_series(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Cria a pasta da serie e grava o titulo.

    O slug e o nome da pasta e nao muda depois; o titulo muda a vontade. Confundir
    os dois e a diferenca entre renomear a serie e reprocessar tudo.
    """
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise Invalid("esperava um objeto com slug e title")

    directory = _series_dir(ctx.cfg, str(payload.get("slug", "")), must_exist=False)
    if directory.exists():
        return HTTPStatus.CONFLICT, {"error": f"serie {directory.name!r} ja existe"}

    title = payload.get("title", directory.name)
    if not isinstance(title, str):
        raise Invalid("title precisa ser texto")

    directory.mkdir(parents=True)
    save_series_meta(ctx.cfg, directory.name, SeriesMeta(title=title or directory.name))
    return HTTPStatus.CREATED, {"slug": directory.name, "title": title or directory.name}


def _get_series_meta(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(ctx.cfg, groups[0])
    return HTTPStatus.OK, load_series_meta(ctx.cfg, directory.name).model_dump(mode="json")


def _put_series_meta(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(ctx.cfg, groups[0])
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise Invalid("esperava um objeto com title, cover e status")

    for field in ("title", "cover", "status"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise Invalid(f"{field} precisa ser texto")

    meta = SeriesMeta.model_validate(payload)
    save_series_meta(ctx.cfg, directory.name, meta)
    return HTTPStatus.OK, load_series_meta(ctx.cfg, directory.name).model_dump(mode="json")


def _put_cover(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Grava a capa da serie, com a extensao que os bytes disserem ser.

    As capas antigas saem junto: `_cover_url` escolhe entre `cover.*` pela ordem
    das extensoes, e deixar duas la significaria trocar a capa sem a troca aparecer.
    """
    directory = _series_dir(ctx.cfg, groups[0])
    suffix = image_suffix(body)
    if suffix is None:
        raise Invalid("o corpo nao e jpeg, png, webp nem bmp")

    for old_cover in directory.glob(f"{COVER_STEM}.*"):
        if old_cover.is_file():
            old_cover.unlink()

    target = directory / f"{COVER_STEM}{suffix}"
    target.write_bytes(body)
    return HTTPStatus.OK, {"cover": target.name, "bytes": len(body)}


def _get_glossary(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(ctx.cfg, groups[0])
    return HTTPStatus.OK, load_glossary(ctx.cfg, directory.name)


def _put_glossary(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Grava o glossario da serie.

    E o arquivo que mais precisa de edicao recorrente: e ele que impede o
    personagem de mudar de nome no capitulo seguinte.
    """
    directory = _series_dir(ctx.cfg, groups[0])
    terms = validate_glossary(json_body(body))
    save_glossary(ctx.cfg, directory.name, terms)
    return HTTPStatus.OK, terms


# ---------- area de espera do upload ----------


def next_chapter_name(existing: Sequence[str]) -> str:
    """O proximo numero de capitulo, na largura que a serie ja usa.

    So capitulo puramente numerico conta. Uma serie com "extra" e "001" continua
    sugerindo "002"; uma serie so com nomes soltos sugere "001" e deixa a escolha
    com quem esta subindo.
    """
    numeric = [name for name in existing if name.isdigit()]
    if not numeric:
        return "001"
    last = max(numeric, key=_natural_key)
    return str(int(last) + 1).zfill(len(last))


def _chapter_paths(cfg: Config, slug: str, chapter: str) -> tuple[Path, Path]:
    """Onde o capitulo mora depois de pronto e enquanto sobe."""
    directory = _series_dir(cfg, slug)
    name = safe_component(chapter)
    if name is None:
        raise Invalid(f"nome de capitulo inaceitavel: {chapter!r}")
    return directory / name, directory / f"{name}{INCOMING_SUFFIX}"


def _incoming_files(incoming: Path) -> list[Path]:
    return list_page_images(incoming) if incoming.is_dir() else []


def extract_archive(data: bytes, target: Path) -> list[str]:
    """Grava as paginas do zip na area de espera, uma entrada por vez.

    Nunca `extractall`: ele obedece ao caminho gravado dentro do zip, e o zip
    carrega o caminho que quiser. A ordem das checagens tambem importa - o veto
    sobre os nomes vem antes de qualquer byte sair, senao a entrada hostil ja
    escreveu quando a recusa acontece.

    O teto do descompactado e conferido duas vezes de proposito: o `file_size`
    declarado antes de abrir, porque um zip de 1MB pode dizer 100GB, e os bytes
    realmente escritos durante a copia, porque quem escreve o declarado e o zip.
    """
    buffer = io.BytesIO(data)
    if not zipfile.is_zipfile(buffer):
        raise Invalid("o corpo nao e um zip; .cbz tambem e zip, o nome nao decide")

    with zipfile.ZipFile(buffer) as archive:
        infos = archive.infolist()
        entries = safe_archive_entries([info.filename for info in infos])
        if entries is None:
            raise Invalid("o arquivo tem entrada com caminho de fuga; nada foi extraido")
        if not entries:
            raise Invalid("o arquivo nao tem nenhuma imagem")
        if len(entries) > MAX_PAGES_PER_CHAPTER:
            raise Invalid(f"{len(entries)} paginas; o teto e {MAX_PAGES_PER_CHAPTER}")

        names = [name for _, name in entries]
        if len(set(names)) != len(names):
            # Duas pastas dentro do zip com a mesma pagina: gravar pelo nome-base
            # faria uma apagar a outra, e o capitulo perderia pagina em silencio.
            raise Invalid("duas entradas do arquivo tem o mesmo nome de pagina")

        declared = sum(infos[index].file_size for index, _ in entries)
        if declared > MAX_ARCHIVE_EXPANDED_BYTES:
            raise Invalid(
                f"o arquivo declara {declared} bytes descompactados;"
                f" o teto e {MAX_ARCHIVE_EXPANDED_BYTES}"
            )

        written = 0
        for index, name in entries:
            with archive.open(infos[index]) as source, (target / name).open("wb") as sink:
                while chunk := source.read(64 * 1024):
                    written += len(chunk)
                    if written > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise Invalid("o descompactado passou do teto no meio da extracao")
                    sink.write(chunk)

    return sorted(names, key=_natural_key)


def _create_chapter(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Abre a area de espera de um capitulo novo.

    Corpo vazio pede sugestao: o maior capitulo numerico que ja existe mais um.
    """
    directory = _series_dir(ctx.cfg, groups[0])
    payload = json_body(body)
    if payload is not None and not isinstance(payload, dict):
        raise Invalid("esperava um objeto com chapter, ou corpo vazio")

    asked = (payload or {}).get("chapter") or next_chapter_name(
        [entry.name for entry in directory.iterdir() if entry.is_dir()]
    )
    if not isinstance(asked, str):
        raise Invalid("chapter precisa ser texto")

    chapter, incoming = _chapter_paths(ctx.cfg, directory.name, asked)
    if chapter.is_dir():
        return HTTPStatus.CONFLICT, {"error": f"capitulo {chapter.name!r} ja existe"}

    incoming.mkdir(exist_ok=True)
    return HTTPStatus.CREATED, {"chapter": chapter.name, "incoming": True, "files": []}


def _put_page(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Grava uma pagina na area de espera.

    Um arquivo por requisicao, corpo cru: `http.server` nao parseia
    `multipart/form-data` e o `cgi`, que parseava, saiu no Python 3.13. De brinde
    isso da barra de progresso por arquivo no front sem esforco nenhum.
    """
    slug, chapter, filename = groups
    name = safe_page_name(filename)
    if name is None:
        raise Invalid(f"nome de pagina inaceitavel: {filename!r}")

    _, incoming = _chapter_paths(ctx.cfg, slug, chapter)
    if not incoming.is_dir():
        raise Invalid("area de espera nao existe; crie o capitulo antes")

    # Pelos bytes, e nao pela extensao: gravar um executavel chamado `1.jpg` na
    # biblioteca seria acreditar no nome que o cliente escolheu.
    if image_suffix(body) is None:
        raise Invalid(f"{name!r} nao e jpeg, png, webp nem bmp")

    existing = _incoming_files(incoming)
    if len(existing) >= MAX_PAGES_PER_CHAPTER and not (incoming / name).exists():
        raise Invalid(
            f"{len(existing)} paginas na area de espera; o teto e {MAX_PAGES_PER_CHAPTER}"
        )

    (incoming / name).write_bytes(body)
    return HTTPStatus.OK, {"file": name, "bytes": len(body)}


def _put_archive(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Extrai um zip/cbz inteiro na area de espera.

    Falha apaga a area de espera toda: meio zip extraido e pior que zip nenhum,
    porque parece capitulo e o `commit` aceitaria.
    """
    slug, chapter = groups
    _, incoming = _chapter_paths(ctx.cfg, slug, chapter)
    if not incoming.is_dir():
        raise Invalid("area de espera nao existe; crie o capitulo antes")

    try:
        names = extract_archive(body, incoming)
    except Exception:
        shutil.rmtree(incoming, ignore_errors=True)
        raise

    return HTTPStatus.OK, {"files": names, "count": len(names)}


def _get_incoming(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """O que ja subiu, na ordem que vai valer na leitura."""
    _, incoming = _chapter_paths(ctx.cfg, groups[0], groups[1])
    files = _incoming_files(incoming)
    return HTTPStatus.OK, {
        "exists": incoming.is_dir(),
        "files": [path.name for path in files],
        "bytes": sum(path.stat().st_size for path in files),
    }


def _delete_incoming(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Descarta a area de espera.

    E a unica remocao que o painel faz, e so apaga o que ele proprio escreveu.
    """
    _, incoming = _chapter_paths(ctx.cfg, groups[0], groups[1])
    if not incoming.is_dir():
        raise Invalid("nao ha area de espera para descartar")

    removed = len(_incoming_files(incoming))
    shutil.rmtree(incoming)
    return HTTPStatus.OK, {"removed": removed}


def _commit_chapter(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Promove a area de espera a capitulo.

    O rename e o unico instante em que o capitulo passa a existir para o resto do
    sistema: ate aqui `discover_chapters` nao o enxerga, entao upload interrompido
    nunca vira meio capitulo traduzido.
    """
    chapter, incoming = _chapter_paths(ctx.cfg, groups[0], groups[1])
    files = _incoming_files(incoming)
    if not files:
        raise Invalid("area de espera vazia; nao ha o que promover")
    if chapter.exists():
        return HTTPStatus.CONFLICT, {"error": f"capitulo {chapter.name!r} ja existe"}

    incoming.rename(chapter)
    return HTTPStatus.OK, {"chapter": chapter.name, "files": [path.name for path in files]}


# ---------- jobs ----------


def _create_job(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Poe um capitulo para processar e devolve na hora.

    202 e nao 200: o trabalho leva minutos e o que volta e um recibo, nao o
    resultado. Quem chamou pergunta o progresso em `GET /api/jobs/<id>`.
    """
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise Invalid("esperava um objeto com series, chapter e engine")

    series = str(payload.get("series", ""))
    chapter = str(payload.get("chapter", ""))
    engine = str(payload.get("engine", "")) or ctx.cfg.translation.engine
    if engine not in available_engines():
        raise Invalid(f"motor {engine!r} nao existe; ha {', '.join(available_engines())}")

    directory, _ = _chapter_paths(ctx.cfg, series, chapter)
    if not directory.is_dir():
        raise Invalid(f"capitulo {series}/{chapter} nao existe; promova a area de espera antes")

    try:
        job = ctx.jobs.start(
            ctx.cfg,
            series=directory.parent.name,
            chapter=directory.name,
            engine=engine,
            force=bool(payload.get("force")),
            dry_run=bool(payload.get("dry_run")),
        )
    except Busy as error:
        return HTTPStatus.CONFLICT, {"error": str(error)}

    return HTTPStatus.ACCEPTED, {"job_id": job.id, "job": job.snapshot()}


def _list_jobs(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    return HTTPStatus.OK, {"jobs": [job.snapshot() for job in ctx.jobs.recent()]}


def _get_job(ctx: Context, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    job = ctx.jobs.get(groups[0])
    if job is None:
        return HTTPStatus.NOT_FOUND, {"error": f"job {groups[0]!r} nao existe neste processo"}
    return HTTPStatus.OK, job.snapshot()


_SLUG = r"([^/]+)"

ROUTES: tuple[Route, ...] = (
    Route("GET", re.compile(r"^/api/health$"), _health),
    Route("GET", re.compile(r"^/api/series$"), _series),
    Route("POST", re.compile(r"^/api/series$"), _create_series, body_required=True),
    Route("GET", re.compile(rf"^/api/series/{_SLUG}/series\.json$"), _get_series_meta),
    Route(
        "PUT",
        re.compile(rf"^/api/series/{_SLUG}/series\.json$"),
        _put_series_meta,
        body_required=True,
    ),
    Route(
        "PUT",
        re.compile(rf"^/api/series/{_SLUG}/cover$"),
        _put_cover,
        MAX_PAGE_BYTES,
        body_required=True,
    ),
    Route("GET", re.compile(rf"^/api/series/{_SLUG}/glossary$"), _get_glossary),
    Route(
        "PUT",
        re.compile(rf"^/api/series/{_SLUG}/glossary$"),
        _put_glossary,
        body_required=True,
    ),
    Route(
        "POST",
        re.compile(rf"^/api/series/{_SLUG}/chapters$"),
        _create_chapter,
    ),
    Route(
        "PUT",
        re.compile(rf"^/api/series/{_SLUG}/chapters/{_SLUG}/files/{_SLUG}$"),
        _put_page,
        MAX_PAGE_BYTES,
        body_required=True,
    ),
    Route(
        "POST",
        re.compile(rf"^/api/series/{_SLUG}/chapters/{_SLUG}/archive$"),
        _put_archive,
        MAX_ARCHIVE_BYTES,
        body_required=True,
    ),
    Route(
        "POST",
        re.compile(rf"^/api/series/{_SLUG}/chapters/{_SLUG}/commit$"),
        _commit_chapter,
    ),
    Route("GET", re.compile(rf"^/api/series/{_SLUG}/chapters/{_SLUG}/incoming$"), _get_incoming),
    Route("GET", re.compile(r"^/api/jobs$"), _list_jobs),
    Route("POST", re.compile(r"^/api/jobs$"), _create_job, body_required=True),
    Route("GET", re.compile(rf"^/api/jobs/{_SLUG}$"), _get_job),
    Route(
        "DELETE",
        re.compile(rf"^/api/series/{_SLUG}/chapters/{_SLUG}/incoming$"),
        _delete_incoming,
    ),
)


def request_path(target: str) -> str:
    """O caminho do pedido, sem query e sem fragmento.

    Sem decodificar: `%2F` precisa continuar dentro de um segmento so, senao
    `/api/series/a%2Fb` casaria como se fossem duas pastas. Quem consome o grupo
    decodifica e passa por `safe_component`, que recusa a barra que aparecer.
    """
    return target.split("?", 1)[0].split("#", 1)[0]


def match_route(method: str, path: str) -> RouteMatch | None:
    """Rota que atende, ou None quando o caminho nao existe."""
    allowed: list[str] = []
    for route in ROUTES:
        found = route.pattern.match(path)
        if found is None:
            continue
        if route.method == method:
            return RouteMatch(route, tuple(unquote(group) for group in found.groups()))
        allowed.append(route.method)

    if allowed:
        return RouteMatch(None, allowed=tuple(allowed))
    return None


# ---------- ligacao HTTP ----------


def make_panel_handler(cfg: Config, jobs: JobRegistry | None = None) -> type[ReaderHandler]:
    """Handler que serve o leitor e, para a propria maquina, tambem o painel.

    Estende o `ReaderHandler`: pedido que nao casa com `/api/` cai no `super()` e
    e servido como arquivo, exatamente como antes.

    `jobs` existe para o teste poder trocar o registro por um que nao dispara o
    pipeline de verdade. Em producao o default e o unico caminho.
    """

    context = Context(cfg=cfg, jobs=jobs or JobRegistry())

    class PanelHandler(ReaderHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(cfg.root), **kwargs)

        def do_GET(self) -> None:  # noqa: N802 - assinatura herdada da stdlib
            if not self._serve_api("GET"):
                super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - assinatura herdada da stdlib
            self._serve_api("POST")

        def do_PUT(self) -> None:  # noqa: N802 - assinatura herdada da stdlib
            self._serve_api("PUT")

        def do_DELETE(self) -> None:  # noqa: N802 - assinatura herdada da stdlib
            self._serve_api("DELETE")

        def _serve_api(self, method: str) -> bool:
            """Atende o pedido se ele for do painel; devolve False para o resto."""
            path = request_path(self.path)
            if not path.startswith(API_PREFIX):
                # Metodo sem arquivo para servir nao tem para onde cair.
                if method != "GET":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "rota nao existe"})
                    return True
                return False

            # Antes de resolver a rota: quem nao e local nao descobre nem quais
            # rotas existem.
            client = self.client_address[0] if self.client_address else ""
            if not is_local_client(client):
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"error": "o painel responde so para a maquina onde o servidor roda"},
                )
                return True

            match = match_route(method, path)
            if match is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"rota {method} {path} nao existe"})
                return True
            if match.route is None:
                self._send_json(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": f"{method} nao vale aqui; use {', '.join(match.allowed)}"},
                    headers={"Allow": ", ".join(match.allowed)},
                )
                return True

            body = self._read_body(match.route)
            if body is None:
                return True

            try:
                status, payload = match.route.handler(context, match.groups, body)
            except Invalid as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return True
            except Exception:  # noqa: BLE001 - erro nosso vira 500, nunca stack na resposta
                self.log_error("falha em %s %s", method, path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "falha no painel"})
                return True

            self._send_json(status, payload)
            return True

        def _read_body(self, route: Route) -> bytes | None:
            """O corpo cru, ou None quando ja respondeu recusando.

            `http.server` nao decodifica `Transfer-Encoding: chunked` e o `cgi`,
            que parseava multipart, saiu no Python 3.13. Por isso o painel exige
            corpo cru com tamanho declarado: o `fetch` do navegador manda
            `Content-Length` para `Blob`, e o que nao manda e outra coisa.

            O teto e conferido no cabecalho, antes de ler um byte - recusar depois
            de receber 500MB nao protege de nada.
            """
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "mande Content-Length"})
                return None

            raw = self.headers.get("Content-Length")
            if raw is None:
                if route.body_required:
                    self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "mande Content-Length"})
                    return None
                return b""

            try:
                length = int(raw)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length nao e numero"})
                return None

            if length > route.max_body:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"corpo de {length} bytes; o teto desta rota e {route.max_body}"},
                )
                return None
            return self.rfile.read(length)

        def _send_json(self, status: int, payload: object, *, headers: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # O painel e estado vivo: resposta cacheada e progresso congelado.
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    return PanelHandler


def serve_panel(cfg: Config, port: int) -> None:
    """Sobe o leitor com o painel junto. So o `mangatl serve` chama isto."""
    serve_handler(make_panel_handler(cfg), port)
