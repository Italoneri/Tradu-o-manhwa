from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from .config import Config
from .panel import (
    MAX_JSON_BYTES,
    ROUTES,
    Invalid,
    image_suffix,
    is_local_client,
    json_body,
    make_panel_handler,
    match_route,
    request_path,
    safe_archive_members,
    safe_component,
    safe_page_name,
    validate_glossary,
)
from .serving import _Server


@pytest.mark.parametrize(
    ("name", "address", "expected"),
    [
        ("loopback ipv4", "127.0.0.1", True),
        ("loopback ipv6", "::1", True),
        ("loopback ipv4 mapeado em ipv6", "::ffff:127.0.0.1", True),
        ("vizinho de wi-fi", "192.168.0.10", False),
        ("outra faixa privada", "10.0.0.5", False),
        ("endereco publico", "8.8.8.8", False),
        ("sem endereco", "", False),
        ("nome que so parece loopback", "127.0.0.1.evil.com", False),
    ],
)
def test_lets_in_only_the_machine_that_runs_the_server(name: str, address: str, expected: bool):
    assert is_local_client(address) is expected, name


@pytest.mark.parametrize(
    ("name", "component", "expected"),
    [
        ("recusa subir um nivel", "..", None),
        ("recusa caminho relativo", "../x", None),
        ("recusa caminho absoluto", "/etc/passwd", None),
        ("recusa caminho do windows", r"C:\x", None),
        ("recusa barra no meio", "a/b", None),
        ("recusa barra invertida no meio", r"a\b", None),
        ("recusa o que o leitor esconde", ".env", None),
        ("recusa vazio", "", None),
        ("recusa o proprio diretorio", ".", None),
        ("recusa caractere de controle", "a\x00b", None),
        ("recusa nome absurdamente longo", "x" * 300, None),
        ("aceita capitulo", "001", "001"),
        ("aceita acento e espaco", "Eu me tornei a Neta Desprezada", "Eu me tornei a Neta Desprezada"),
        ("aceita ponto no meio", "vol.2", "vol.2"),
        ("aceita o limite exato", "x" * 120, "x" * 120),
    ],
)
def test_accepts_only_a_name_safe_as_a_folder(name: str, component: str, expected: str | None):
    assert safe_component(component) == expected, name


@pytest.mark.parametrize(
    ("name", "page", "expected"),
    [
        ("aceita jpg", "p0001.jpg", "p0001.jpg"),
        ("aceita maiuscula na extensao", "x.JPG", "x.JPG"),
        ("aceita png", "1.png", "1.png"),
        ("recusa executavel", "x.exe", None),
        ("recusa extensao dupla", "x.jpg.exe", None),
        ("recusa sem extensao", "x", None),
        ("recusa caminho", "a/x.jpg", None),
        ("recusa oculto", ".x.jpg", None),
    ],
)
def test_accepts_only_an_image_as_a_page(name: str, page: str, expected: str | None):
    assert safe_page_name(page) == expected, name


@pytest.mark.parametrize(
    ("name", "members", "expected"),
    [
        ("aceita paginas soltas", ["1.jpg", "2.jpg"], ["1.jpg", "2.jpg"]),
        ("aceita paginas dentro de uma pasta", ["cap/1.jpg", "cap/2.png"], ["1.jpg", "2.png"]),
        ("ignora a entrada de diretorio", ["cap/", "cap/1.jpg"], ["1.jpg"]),
        ("ignora o lixo do finder", ["__MACOSX/._1.jpg", "1.jpg"], ["1.jpg"]),
        ("ignora o que nao e pagina", ["leiame.txt", "1.jpg"], ["1.jpg"]),
        ("recusa fuga para fora da pasta", ["../../.env"], None),
        ("recusa fuga no meio do caminho", ["cap/../../x.jpg"], None),
        ("recusa caminho absoluto", ["/etc/passwd"], None),
        ("recusa caminho do windows", [r"C:\x.jpg"], None),
        ("uma entrada hostil condena o zip inteiro", ["1.jpg", "../../.env"], None),
        ("zip sem pagina nenhuma sai vazio", ["leiame.txt"], []),
    ],
)
def test_extracts_only_harmless_archive_entries(
    name: str, members: list[str], expected: list[str] | None
):
    assert safe_archive_members(members) == expected, name


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/health"),
        ("GET", "/api/series"),
        ("POST", "/api/series"),
        ("GET", "/api/series/obra/series.json"),
        ("PUT", "/api/series/obra/series.json"),
        ("PUT", "/api/series/obra/cover"),
        ("GET", "/api/series/obra/glossary"),
        ("PUT", "/api/series/obra/glossary"),
    ],
)
def test_routes_every_declared_path(method: str, path: str):
    match = match_route(method, path)
    assert match is not None and match.route is not None, f"{method} {path}"


def test_declares_no_duplicate_route():
    keys = [(route.method, route.pattern.pattern) for route in ROUTES]
    assert len(keys) == len(set(keys))


def test_captures_the_slug_from_the_path():
    match = match_route("GET", "/api/series/Eu%20me%20tornei/glossary")

    assert match is not None
    assert match.groups == ("Eu me tornei",)


def test_answers_405_and_not_404_for_the_wrong_method():
    match = match_route("DELETE", "/api/health")

    assert match is not None
    assert match.route is None
    assert match.allowed == ("GET",)


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("glossario comum", {"Zhuge": "Zhuge", "Sect Master": "Mestre da Seita"}, None),
        ("vazio passa", {}, None),
        ("recusa lista", ["a"], "objeto JSON"),
        ("recusa texto solto", "Zhuge", "objeto JSON"),
        ("recusa termo vazio", {"": "x"}, "nao pode ser vazio"),
        ("recusa termo so de espaco", {"  ": "x"}, "nao pode ser vazio"),
        ("recusa traducao que nao e texto", {"Zhuge": 3}, "precisa ser texto"),
        ("recusa dicionario inteiro", {str(n): "x" for n in range(501)}, "o teto e 500"),
    ],
)
def test_validates_the_glossary_at_the_edge(name: str, payload: object, expected: str | None):
    if expected is None:
        assert validate_glossary(payload) == payload, name
        return
    with pytest.raises(Invalid, match=expected):
        validate_glossary(payload)


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("jpeg", b"\xff\xd8\xff\xe0" + b"0" * 20, ".jpg"),
        ("png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, ".png"),
        ("bmp", b"BM" + b"0" * 20, ".bmp"),
        ("webp", b"RIFF" + b"0000" + b"WEBP" + b"0" * 20, ".webp"),
        ("executavel disfarcado", b"MZ" + b"0" * 20, None),
        ("texto", b"nao sou imagem", None),
        ("vazio", b"", None),
    ],
)
def test_reads_the_format_from_the_bytes(name: str, data: bytes, expected: str | None):
    # Do conteudo e nao do Content-Type: o cabecalho e do cliente.
    assert image_suffix(data) == expected, name


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        ("objeto", b'{"a": 1}', {"a": 1}),
        ("corpo vazio vira nulo", b"", None),
    ],
)
def test_parses_the_json_body(name: str, body: bytes, expected: object):
    assert json_body(body) == expected, name


def test_refuses_a_body_that_is_not_json():
    with pytest.raises(Invalid, match="nao e JSON"):
        json_body(b"{nao")


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("caminho que nao existe", "/api/nao-existe"),
        ("prefixo parecido", "/api/healthz"),
        ("arquivo comum", "/reader/app.js"),
    ],
)
def test_reports_no_route_for_an_unknown_path(name: str, path: str):
    assert match_route("GET", path) is None, name


def test_keeps_an_encoded_slash_inside_one_segment():
    # `%2F` decodificado cedo viraria separador e `/api/series/a%2Fb` casaria como
    # se fossem duas pastas. O caminho e casado cru; quem le o grupo decodifica e
    # passa por `safe_component`, que recusa a barra.
    assert request_path("/api/series/a%2Fb?x=1") == "/api/series/a%2Fb"
    assert safe_component("a/b") is None


@pytest.mark.parametrize(
    ("name", "target", "expected"),
    [
        ("tira a query", "/api/health?x=1", "/api/health"),
        ("tira o fragmento", "/api/health#topo", "/api/health"),
        ("deixa o caminho limpo em paz", "/api/health", "/api/health"),
    ],
)
def test_strips_query_and_fragment(name: str, target: str, expected: str):
    assert request_path(target) == expected, name


@pytest.fixture
def panel_server(tmp_path: Path):
    """Sobe o handler real numa porta efemera, para provar a ligacao e nao so as funcoes."""
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-secreta\n", encoding="utf-8")
    (tmp_path / "reader").mkdir()
    (tmp_path / "reader" / "app.js").write_text("export const ok = 1;\n", encoding="utf-8")
    (tmp_path / "library").mkdir()
    (tmp_path / "output").mkdir()

    cfg = Config(root=tmp_path)
    with _Server(("127.0.0.1", 0), make_panel_handler(cfg)) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


def _get(port: int, path: str, method: str = "GET") -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, body


def test_reports_what_the_machine_can_do(panel_server: int):
    status, body = _get(panel_server, "/api/health")
    payload = json.loads(body)

    assert status == 200
    assert payload["ok"] is True
    assert "free" in payload["engines"]
    assert isinstance(payload["has_api_key"], bool)


def test_never_answers_with_the_api_key(panel_server: int):
    # A chave esta no ambiente do processo de teste ou nao; o que nao pode e o
    # valor sair numa resposta.
    _, body = _get(panel_server, "/api/health")
    assert b"sk-" not in body


def test_lists_the_disk_and_not_the_reader_index(panel_server: int, tmp_path: Path):
    # Uma serie com capitulo enviado e nao traduzido: o painel precisa ve-la para
    # oferecer o botao que a traduz, e o leitor nao pode lista-la porque nao ha o
    # que abrir.
    chapter = tmp_path / "library" / "Obra" / "001"
    chapter.mkdir(parents=True)
    (chapter / "1.jpg").write_bytes(b"0")

    status, body = _get(panel_server, "/api/series")
    payload = json.loads(body)

    assert status == 200
    assert [s["series"] for s in payload["series"]] == ["Obra"]
    assert payload["series"][0]["chapters"][0]["image_count"] == 1
    assert payload["series"][0]["chapters"][0]["engines"] == []


def test_lists_an_empty_library_as_empty(panel_server: int):
    status, body = _get(panel_server, "/api/series")

    assert status == 200
    assert json.loads(body)["series"] == []


def test_shows_a_series_created_by_the_panel_right_away(panel_server: int):
    # Sem isso a tela criaria a serie e ela sumiria ate ter capitulo traduzido.
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Recem-criada"}).encode("utf-8"))

    _, body = _get(panel_server, "/api/series")

    assert [s["series"] for s in json.loads(body)["series"]] == ["Recem-criada"]


def test_keeps_serving_the_reader_next_to_the_panel(panel_server: int):
    assert _get(panel_server, "/reader/app.js")[0] == 200
    assert _get(panel_server, "/.env")[0] == 404


def test_refuses_the_wrong_method_on_a_real_route(panel_server: int):
    status, body = _get(panel_server, "/api/health", method="POST")

    assert status == 405
    assert b"GET" in body


def test_refuses_a_write_method_outside_the_panel(panel_server: int):
    # Sem rota e sem arquivo para servir: PUT em caminho de leitor nao tem para
    # onde cair.
    assert _get(panel_server, "/reader/app.js", method="PUT")[0] == 404


# ---------- rotas de escrita, pelo servidor de verdade ----------

PNG = bytes.fromhex("89504e470d0a1a0a") + b"0" * 32
JPEG = bytes.fromhex("ffd8ffe0") + b"0" * 32


def _send(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    *,
    declare_length: bool = True,
) -> tuple[int, bytes]:
    """Pedido cru, para poder omitir o Content-Length de proposito."""
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest(method, path)
    if body is not None and declare_length:
        connection.putheader("Content-Length", str(len(body)))
    connection.endheaders()
    if body is not None and declare_length:
        connection.send(body)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, payload


def _put_json(port: int, path: str, payload: object) -> tuple[int, bytes]:
    return _send(port, "PUT", path, json.dumps(payload).encode("utf-8"))


def test_creates_a_series_with_slug_and_title(panel_server: int, tmp_path: Path):
    status, body = _send(
        panel_server,
        "POST",
        "/api/series",
        json.dumps({"slug": "Obra Nova", "title": "Obra Nova, o Titulo"}).encode("utf-8"),
    )

    assert status == 201
    assert json.loads(body)["slug"] == "Obra Nova"
    assert (tmp_path / "library" / "Obra Nova" / "series.json").is_file()


def test_refuses_to_create_a_series_twice(panel_server: int):
    payload = json.dumps({"slug": "Repetida"}).encode("utf-8")
    assert _send(panel_server, "POST", "/api/series", payload)[0] == 201
    assert _send(panel_server, "POST", "/api/series", payload)[0] == 409


@pytest.mark.parametrize(
    ("name", "slug"),
    [("fuga", "../fora"), ("oculta", ".git"), ("vazia", "")],
)
def test_refuses_a_hostile_series_name(panel_server: int, name: str, slug: str):
    status, body = _send(
        panel_server, "POST", "/api/series", json.dumps({"slug": slug}).encode("utf-8")
    )

    assert status == 422, name
    assert b"inaceitavel" in body, name


def test_keeps_the_glossary_across_a_write_and_a_read(panel_server: int):
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Obra"}).encode("utf-8"))
    terms = {"Zhuge": "Zhuge", "Sect Master": "Mestre da Seita"}

    assert _put_json(panel_server, "/api/series/Obra/glossary", terms)[0] == 200

    status, body = _send(panel_server, "GET", "/api/series/Obra/glossary")
    assert status == 200
    assert json.loads(body) == terms


def test_answers_422_and_not_500_for_a_broken_glossary(panel_server: int):
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Obra"}).encode("utf-8"))

    status, body = _put_json(panel_server, "/api/series/Obra/glossary", ["nao", "e", "objeto"])

    assert status == 422
    assert b"objeto JSON" in body


def test_answers_422_for_a_series_that_does_not_exist(panel_server: int):
    status, body = _send(panel_server, "GET", "/api/series/nao-existe/glossary")

    assert status == 422
    assert b"nao existe" in body


def test_writes_the_cover_with_the_suffix_the_bytes_ask_for(panel_server: int, tmp_path: Path):
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Obra"}).encode("utf-8"))

    assert _send(panel_server, "PUT", "/api/series/Obra/cover", PNG)[0] == 200
    assert (tmp_path / "library" / "Obra" / "cover.png").is_file()


def test_replaces_the_old_cover_instead_of_stacking_one(panel_server: int, tmp_path: Path):
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Obra"}).encode("utf-8"))
    _send(panel_server, "PUT", "/api/series/Obra/cover", PNG)
    _send(panel_server, "PUT", "/api/series/Obra/cover", JPEG)

    covers = sorted(p.name for p in (tmp_path / "library" / "Obra").glob("cover.*"))
    assert covers == ["cover.jpg"]


def test_refuses_a_cover_that_is_not_an_image(panel_server: int):
    _send(panel_server, "POST", "/api/series", json.dumps({"slug": "Obra"}).encode("utf-8"))

    status, body = _send(panel_server, "PUT", "/api/series/Obra/cover", b"MZ" + b"0" * 40)

    assert status == 422
    assert b"nao e jpeg" in body


def test_demands_a_declared_length_on_a_write(panel_server: int):
    # `http.server` nao decodifica chunked e o painel nao adivinha tamanho.
    status, _ = _send(panel_server, "PUT", "/api/series/Obra/glossary", b"{}", declare_length=False)
    assert status == 411


def test_refuses_a_body_over_the_route_ceiling_before_reading_it(panel_server: int):
    connection = HTTPConnection("127.0.0.1", panel_server, timeout=5)
    connection.putrequest("PUT", "/api/series/Obra/glossary")
    connection.putheader("Content-Length", str(MAX_JSON_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    status = response.status
    connection.close()

    assert status == 413


def test_shows_the_title_from_series_json(panel_server: int, tmp_path: Path):
    _send(
        panel_server,
        "POST",
        "/api/series",
        json.dumps({"slug": "obra-slug", "title": "O Titulo Bonito"}).encode("utf-8"),
    )

    status, body = _send(panel_server, "GET", "/api/series/obra-slug/series.json")

    assert status == 200
    assert json.loads(body)["title"] == "O Titulo Bonito"
