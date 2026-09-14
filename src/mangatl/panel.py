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

import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import unquote

from .config import Config
from .engines.base import available_engines
from .models import SeriesMeta
from .serving import ReaderHandler, serve_handler
from .store import (
    COVER_STEM,
    IMAGE_SUFFIXES,
    build_library,
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


def safe_archive_members(names: Sequence[str]) -> list[str] | None:
    """Entradas de um zip que podem ser extraidas, ou None se alguma for hostil.

    Zip carrega caminho dentro de si e a stdlib extrai o que estiver escrito -
    inclusive `../../.env`. Uma entrada hostil condena o arquivo inteiro em vez de
    ser pulada: zip com caminho de fuga nao e capitulo mal montado, e continuar
    extraindo o resto seria tratar ataque como tropeco.

    Diretorio e ignorado, assim como o `__MACOSX/` que o Finder enfia em todo zip.
    Entrada que nao e imagem tambem e ignorada - nao e hostil, so nao e pagina.
    """
    kept: list[str] = []
    for raw in names:
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
            kept.append(page)
    return kept


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

Handler = Callable[[Config, tuple[str, ...], bytes], tuple[int, object]]


class Route(NamedTuple):
    method: str
    pattern: re.Pattern[str]
    handler: Handler
    max_body: int = MAX_JSON_BYTES
    """Teto do corpo, conferido pelo `Content-Length` antes de ler um byte."""


class RouteMatch(NamedTuple):
    """Resultado do casamento de rota.

    `route` None significa caminho certo e metodo errado, que e 405 e nao 404:
    dizer "nao existe" para `POST /api/health` esconderia o erro de quem chamou.
    """

    route: Route | None
    groups: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()


def _health(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    return HTTPStatus.OK, {
        "ok": True,
        "root": str(cfg.root),
        "engines": available_engines(),
        "detector": cfg.detect.backend,
        # Booleano, nunca o valor: a chave nao sai desta maquina por resposta nenhuma.
        "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "python": sys.version.split()[0],
    }


def _series(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """O mesmo conteudo de library.json, gerado na hora.

    Na hora e nao lido do disco porque o painel mostra a verdade do disco; o
    `library.json` salvo continua sendo o que o leitor consome.
    """
    return HTTPStatus.OK, build_library(cfg).model_dump(mode="json")


def _series_dir(cfg: Config, slug: str, *, must_exist: bool = True) -> Path:
    name = safe_component(slug)
    if name is None:
        raise Invalid(f"nome de serie inaceitavel: {slug!r}")
    directory = cfg.library_dir / name
    if must_exist and not directory.is_dir():
        raise Invalid(f"serie {name!r} nao existe")
    return directory


def _create_series(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Cria a pasta da serie e grava o titulo.

    O slug e o nome da pasta e nao muda depois; o titulo muda a vontade. Confundir
    os dois e a diferenca entre renomear a serie e reprocessar tudo.
    """
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise Invalid("esperava um objeto com slug e title")

    directory = _series_dir(cfg, str(payload.get("slug", "")), must_exist=False)
    if directory.exists():
        return HTTPStatus.CONFLICT, {"error": f"serie {directory.name!r} ja existe"}

    title = payload.get("title", directory.name)
    if not isinstance(title, str):
        raise Invalid("title precisa ser texto")

    directory.mkdir(parents=True)
    save_series_meta(cfg, directory.name, SeriesMeta(title=title or directory.name))
    return HTTPStatus.CREATED, {"slug": directory.name, "title": title or directory.name}


def _get_series_meta(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(cfg, groups[0])
    return HTTPStatus.OK, load_series_meta(cfg, directory.name).model_dump(mode="json")


def _put_series_meta(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(cfg, groups[0])
    payload = json_body(body)
    if not isinstance(payload, dict):
        raise Invalid("esperava um objeto com title, cover e status")

    for field in ("title", "cover", "status"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise Invalid(f"{field} precisa ser texto")

    meta = SeriesMeta.model_validate(payload)
    save_series_meta(cfg, directory.name, meta)
    return HTTPStatus.OK, load_series_meta(cfg, directory.name).model_dump(mode="json")


def _put_cover(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Grava a capa da serie, com a extensao que os bytes disserem ser.

    As capas antigas saem junto: `_cover_url` escolhe entre `cover.*` pela ordem
    das extensoes, e deixar duas la significaria trocar a capa sem a troca aparecer.
    """
    directory = _series_dir(cfg, groups[0])
    suffix = image_suffix(body)
    if suffix is None:
        raise Invalid("o corpo nao e jpeg, png, webp nem bmp")

    for old_cover in directory.glob(f"{COVER_STEM}.*"):
        if old_cover.is_file():
            old_cover.unlink()

    target = directory / f"{COVER_STEM}{suffix}"
    target.write_bytes(body)
    return HTTPStatus.OK, {"cover": target.name, "bytes": len(body)}


def _get_glossary(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    directory = _series_dir(cfg, groups[0])
    return HTTPStatus.OK, load_glossary(cfg, directory.name)


def _put_glossary(cfg: Config, groups: tuple[str, ...], body: bytes) -> tuple[int, object]:
    """Grava o glossario da serie.

    E o arquivo que mais precisa de edicao recorrente: e ele que impede o
    personagem de mudar de nome no capitulo seguinte.
    """
    directory = _series_dir(cfg, groups[0])
    terms = validate_glossary(json_body(body))
    save_glossary(cfg, directory.name, terms)
    return HTTPStatus.OK, terms


_SLUG = r"([^/]+)"

ROUTES: tuple[Route, ...] = (
    Route("GET", re.compile(r"^/api/health$"), _health),
    Route("GET", re.compile(r"^/api/series$"), _series),
    Route("POST", re.compile(r"^/api/series$"), _create_series),
    Route("GET", re.compile(rf"^/api/series/{_SLUG}/series\.json$"), _get_series_meta),
    Route("PUT", re.compile(rf"^/api/series/{_SLUG}/series\.json$"), _put_series_meta),
    Route("PUT", re.compile(rf"^/api/series/{_SLUG}/cover$"), _put_cover, MAX_PAGE_BYTES),
    Route("GET", re.compile(rf"^/api/series/{_SLUG}/glossary$"), _get_glossary),
    Route("PUT", re.compile(rf"^/api/series/{_SLUG}/glossary$"), _put_glossary),
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


def make_panel_handler(cfg: Config) -> type[ReaderHandler]:
    """Handler que serve o leitor e, para a propria maquina, tambem o painel.

    Estende o `ReaderHandler`: pedido que nao casa com `/api/` cai no `super()` e
    e servido como arquivo, exatamente como antes.
    """

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

            body = self._read_body(method, match.route.max_body)
            if body is None:
                return True

            try:
                status, payload = match.route.handler(cfg, match.groups, body)
            except Invalid as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return True
            except Exception:  # noqa: BLE001 - erro nosso vira 500, nunca stack na resposta
                self.log_error("falha em %s %s", method, path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "falha no painel"})
                return True

            self._send_json(status, payload)
            return True

        def _read_body(self, method: str, limit: int) -> bytes | None:
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
                if method in {"POST", "PUT"}:
                    self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "mande Content-Length"})
                    return None
                return b""

            try:
                length = int(raw)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Content-Length nao e numero"})
                return None

            if length > limit:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"corpo de {length} bytes; o teto desta rota e {limit}"},
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
