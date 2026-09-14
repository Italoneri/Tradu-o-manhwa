from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from .config import Config
from .panel import (
    ROUTES,
    is_local_client,
    make_panel_handler,
    match_route,
    request_path,
    safe_archive_members,
    safe_component,
    safe_page_name,
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


def test_routes_the_known_paths():
    for method, pattern, handler in ROUTES:
        path = pattern.pattern.strip("^$")
        match = match_route(method, path)
        assert match is not None and match.handler is handler, path


def test_answers_405_and_not_404_for_the_wrong_method():
    match = match_route("POST", "/api/health")

    assert match is not None
    assert match.handler is None
    assert match.allowed == ("GET",)


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


def test_builds_the_library_from_disk(panel_server: int):
    status, body = _get(panel_server, "/api/series")
    payload = json.loads(body)

    assert status == 200
    assert payload["series"] == []
    assert payload["library_base"] == "library"


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
