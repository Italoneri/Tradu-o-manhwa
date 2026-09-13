#!/usr/bin/env python3
"""Compara duas extracoes pagina a pagina.

    python scripts/compare_extractions.py <baseline.json> <novo.json>

A troca de detector muda uma variavel do pipeline; este script mede o efeito.
O numero que mais importa nao e quanto se ganhou: e quanto se perdeu. Ganhar
falas novas e barato de ver nos PNGs de debug, mas uma fala que sumiu de uma
pagina que antes estava certa passa despercebida se ninguem contar.

So stdlib, entao roda sem o venv do projeto.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

TOP = 10


def load(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {page["image"]: page["blocks"] for page in data["pages"]}


def mean_confidence(pages: dict[str, list[dict]]) -> float:
    confidences = [block["confidence"] for blocks in pages.values() for block in blocks]
    return sum(confidences) / len(confidences) if confidences else 0.0


def line(label: str, before: object, after: object) -> str:
    return f"{label:<28} {str(before):>8}  ->  {str(after):>8}"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    before, after = load(Path(sys.argv[1])), load(Path(sys.argv[2]))
    shared = sorted(before.keys() & after.keys())
    if missing := (before.keys() ^ after.keys()):
        print(f"aviso: {len(missing)} pagina(s) so existem num dos dois arquivos\n")

    deltas = {name: len(after[name]) - len(before[name]) for name in shared}
    gained = {name: delta for name, delta in deltas.items() if delta > 0}
    lost = {name: delta for name, delta in deltas.items() if delta < 0}

    print(line("blocos", sum(len(b) for b in before.values()), sum(len(b) for b in after.values())))
    print(line("paginas com zero falas", sum(not b for b in before.values()), sum(not b for b in after.values())))
    print(line("confianca media do ocr", f"{mean_confidence(before):.1f}", f"{mean_confidence(after):.1f}"))
    print()
    print(f"paginas que ganharam falas   {len(gained):>4}  (+{sum(gained.values())} blocos)")
    print(f"paginas que perderam falas   {len(lost):>4}  ({sum(lost.values())} blocos)  <- regressao")
    print(f"paginas inalteradas          {len(shared) - len(gained) - len(lost):>4}")
    print()

    kinds = Counter(block.get("kind", "bubble") for blocks in after.values() for block in blocks)
    print("classe dos blocos novos:", dict(kinds.most_common()))
    print()

    for title, subset, reverse in (("maiores ganhos", gained, True), ("maiores perdas", lost, False)):
        print(f"{title}:")
        for name, delta in sorted(subset.items(), key=lambda item: item[1], reverse=reverse)[:TOP]:
            print(f"  {name:<14} {len(before[name])} -> {len(after[name])}  ({delta:+d})")
        if not subset:
            print("  nenhuma")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
