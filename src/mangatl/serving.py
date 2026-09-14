"""O que o leitor pode baixar pela rede, e nada alem disso.

O servidor escuta em 0.0.0.0 para o celular alcancar, e a raiz do projeto guarda
o .env com a chave da Anthropic. Servir a raiz inteira publica essa chave para
qualquer um no mesmo Wi-Fi, e a listagem de diretorio da raiz denuncia o arquivo
antes mesmo de alguem adivinhar o nome.

Sem dependencia fora da stdlib de proposito: o `scripts/serve.py` roda no python
do Windows, que nao enxerga o venv do projeto.
"""

from __future__ import annotations

import http.server
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote

SERVABLE_ROOTS = ("reader", "output", "library")
"""A PWA, os JSONs e as imagens das paginas - tudo que o leitor busca."""


def _segments(request_path: str) -> list[str]:
    """Segmentos do caminho pedido, ja sem query e sem percent-encoding.

    Decodifica antes de olhar: `/%2Eenv` e `/.env` sao o mesmo arquivo, e so o
    primeiro passa por uma comparacao literal. A barra invertida conta como
    separador porque o NTFS a trata como tal, embora o http.server nao trate.
    """
    path = request_path.split("?", 1)[0].split("#", 1)[0]
    return [segment for segment in unquote(path).replace("\\", "/").split("/") if segment]


def is_servable(request_path: str) -> bool:
    """Se os bytes desse caminho podem sair na rede.

    A raiz responde False por nao ter nada para servir: ela lista o .env. Quem
    chama redireciona para /reader/ antes de perguntar.
    """
    segments = _segments(request_path)
    if not segments:
        return False
    # Cobre .env, .git e tambem `..`, que e o caminho de fuga para fora da raiz.
    if any(segment.startswith(".") for segment in segments):
        return False
    return segments[0] in SERVABLE_ROOTS


class ReaderHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler com `is_servable` na frente.

    O filtro entra em `send_head` porque e por onde GET e HEAD passam os dois,
    e antes do `translate_path` - que resolveria o `..` e apagaria a evidencia.
    """

    def send_head(self):  # noqa: ANN201 - assinatura herdada da stdlib
        if not _segments(self.path):
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/reader/")
            self.end_headers()
            return None
        if not is_servable(self.path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        return super().send_head()


def make_handler(root: Path) -> type[ReaderHandler]:
    class RootedReaderHandler(ReaderHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

    return RootedReaderHandler


class _Server(http.server.ThreadingHTTPServer):
    """Uma thread por conexao.

    O servidor de uma conexao por vez basta para servir arquivo, mas nao para o
    painel: um job de traducao leva minutos e, com o servidor bloqueado, o
    navegador nao consegue nem buscar o progresso nem carregar a pagina - a tela
    congela e parece que travou.
    """

    allow_reuse_address = True
    daemon_threads = True


def serve_handler(handler: type[http.server.BaseHTTPRequestHandler], port: int) -> None:
    """Bloqueia servindo com `handler` na porta, ate KeyboardInterrupt."""
    with _Server(("0.0.0.0", port), handler) as httpd:
        httpd.serve_forever()


def serve_reader(root: Path, port: int) -> None:
    """Bloqueia servindo `root` na porta, ate KeyboardInterrupt.

    Somente leitura: e o que o `scripts/serve.py` sobe no python do Windows, sem
    venv. O painel entra por outro caminho, em `panel.py`.
    """
    serve_handler(make_handler(root), port)
