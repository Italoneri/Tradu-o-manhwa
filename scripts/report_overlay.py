#!/usr/bin/env python3
"""Acha os baloes onde o overlay tem mais chance de estar feio.

    python scripts/report_overlay.py <chapter.json>

Ninguem consegue olhar 155 fatias no olho procurando problema. Duas razoes
apontam os casos que valem abrir:

    area do balao / area do texto   onde mais branco sobra do que o necessario
    caracteres pt / caracteres en   onde a traducao tem mais chance de transbordar

A primeira nao e defeito por si: balao redondo com uma palavra sempre vai ter
razao alta, porque o desenhista deixou ar em volta do texto. Ela vira defeito
quando o branco acompanha a bbox em vez do texto - e serve para conferir que
nao acompanha mais.

So stdlib, entao roda sem o venv do projeto.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

TOP = 15

# O JSON e UTF-8 e as falas tem acento; o console do Windows abre em cp1252 e
# troca cada acento por '?'. Reconfigurar aqui vale mais que pedir chcp a quem roda.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def blocks_of(data: dict) -> list[tuple[dict, dict]]:
    return [(page, block) for page in data["pages"] for block in page["blocks"]]


def area(box: dict) -> int:
    return box["w"] * box["h"]


def summary(label: str, values: list[float]) -> str:
    if not values:
        return f"{label:<30} sem dados"
    ordered = sorted(values)
    quartiles = statistics.quantiles(ordered, n=4) if len(ordered) > 3 else [float("nan")] * 3
    return (
        f"{label:<30} n={len(ordered):<4} "
        f"mediana={statistics.median(ordered):>6.2f}  "
        f"q3={quartiles[2]:>6.2f}  max={ordered[-1]:>6.2f}"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    entries = blocks_of(data)
    if not entries:
        print("capitulo sem blocos")
        return 1

    measured = [(page, block) for page, block in entries if block.get("text_bbox")]
    print(f"{data['series']} {data['chapter']} - motor {data['engine']}, pipeline v{data['pipeline_version']}")
    print(f"blocos {len(entries)}, com text_bbox {len(measured)}")
    print()

    white = [
        (area(block["bbox"]) / area(block["text_bbox"]), page, block)
        for page, block in measured
        if block.get("bbox")
    ]
    growth = [
        (len(block["text"]) / len(block["source_text"]), page, block)
        for page, block in entries
        if block["source_text"]
    ]

    print(summary("area balao / area texto", [ratio for ratio, _, _ in white]))
    print(summary("caracteres pt / en", [ratio for ratio, _, _ in growth]))
    if block_fonts := [block["source_font_px"] for _, block in entries if block.get("source_font_px")]:
        print(summary("corpo original em px", [float(px) for px in block_fonts]))
    print()

    for title, rows in (("mais branco sobrando", white), ("mais chance de transbordar", growth)):
        print(f"{title} (top {TOP}):")
        for ratio, page, block in sorted(rows, key=lambda row: row[0], reverse=True)[:TOP]:
            print(f"  {ratio:>6.2f}x  fatia {page['index']:>3}  {block['text'][:52]!r}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
