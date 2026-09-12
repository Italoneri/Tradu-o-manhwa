#!/usr/bin/env python3
"""Serve o leitor pelo python do Windows, com o mesmo filtro do `mangatl serve`.

Por que este script existe, em vez de `python -m http.server`: o celular precisa
alcancar o PC, e o IP que o `mangatl serve` imprime dentro do WSL e o endereco
interno (172.x.x.x), que o celular nao enxerga. A saida e servir pelo Windows -
mas ai o `mangatl` nao esta instalado, e o `http.server` puro publica a raiz do
projeto inteira, .env com a chave da Anthropic junto.

So stdlib, entao o python do Windows roda sem o venv do projeto: importa o
filtro de `src/mangatl/serving.py` em vez de repeti-lo aqui.

    python scripts\\serve.py [porta]
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mangatl.serving import serve_reader  # noqa: E402 - depende do sys.path acima


def _lan_addresses() -> list[str]:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return [probe.getsockname()[0]]
    except OSError:
        return []
    finally:
        probe.close()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"leitor:   http://localhost:{port}/reader/")
    for address in _lan_addresses():
        print(f"celular:  http://{address}:{port}/reader/")
    print("Ctrl+C para parar")
    try:
        serve_reader(ROOT, port)
    except KeyboardInterrupt:
        print("\nparado")


if __name__ == "__main__":
    main()
