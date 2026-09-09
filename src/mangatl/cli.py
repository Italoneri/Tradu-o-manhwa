"""Interface de linha de comando do mangatl."""

from __future__ import annotations

import http.server
import logging
import shutil
import socket
import socketserver
import subprocess
from pathlib import Path

import typer
from dotenv import load_dotenv

from .config import Config, load_config
from .engines.base import TranslationError, UnknownEngineError, available_engines, create_engine
from .pipeline import ChapterNotFoundError, extract_chapter, translate_chapter
from .store import build_library, discover_chapters, save_library

app = typer.Typer(add_completion=False, help="Traduz capitulos de manga/mahua EN->PT e serve um leitor web.")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )


def _load() -> Config:
    load_dotenv()
    try:
        return load_config()
    except FileNotFoundError as error:
        typer.secho(str(error), fg=typer.colors.RED)
        raise typer.Exit(code=2) from error


def _resolve_chapter(cfg: Config, target: str) -> tuple[str, str]:
    """Aceita `library/serie/001`, `serie/001` ou um caminho absoluto."""
    path = Path(target)
    candidate = path if path.is_absolute() else (cfg.root / path)
    if not candidate.is_dir():
        candidate = cfg.library_dir / target
    if not candidate.is_dir():
        raise ChapterNotFoundError(f"capitulo nao encontrado: {target}")

    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(cfg.library_dir.resolve())
    except ValueError as error:
        raise ChapterNotFoundError(f"{resolved} esta fora de {cfg.library_dir}") from error

    if len(relative.parts) != 2:
        raise ChapterNotFoundError(f"esperava <serie>/<capitulo>, recebi {relative}")
    return relative.parts[0], relative.parts[1]


def _process_one(
    cfg: Config,
    series: str,
    chapter: str,
    *,
    engine_name: str,
    force: bool,
    debug_boxes: bool,
    dry_run: bool,
) -> None:
    report = extract_chapter(cfg, series, chapter, force=force, debug_boxes=debug_boxes)
    typer.secho(
        f"{series}/{chapter}: {len(report.extraction.pages)} paginas "
        f"({report.extracted_pages} extraidas, {report.reused_pages} reaproveitadas), "
        f"{report.block_count} baloes",
        fg=typer.colors.CYAN,
    )
    if debug_boxes:
        typer.echo(f"  caixas desenhadas em {cfg.output_dir / series / chapter / 'debug'}")

    if dry_run:
        typer.secho("  --dry-run: parei antes de traduzir", fg=typer.colors.YELLOW)
        return

    engine = create_engine(engine_name, cfg)
    result = translate_chapter(cfg, report.extraction, engine)
    lines = sum(len(page.blocks) for page in result.pages)
    typer.secho(f"  traduzido com '{engine.name}': {lines} falas", fg=typer.colors.GREEN)


@app.command()
def process(
    target: str = typer.Argument(..., help="Pasta do capitulo, ex: library/minha-serie/001"),
    engine: str = typer.Option(None, "--engine", "-e", help=f"Motor de traducao: {', '.join(available_engines())}"),
    model: str = typer.Option(None, "--model", "-m", help="Sobrescreve o modelo do config.toml"),
    force: bool = typer.Option(False, "--force", help="Refaz o OCR mesmo em paginas inalteradas"),
    debug_boxes: bool = typer.Option(False, "--debug-boxes", help="Desenha as caixas detectadas em output/.../debug"),
    dry_run: bool = typer.Option(False, "--dry-run", help="So extrai; nao chama motor de traducao"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Processa um capitulo: deteccao, OCR e traducao."""
    _configure_logging(verbose)
    cfg = _load()
    if model:
        cfg = cfg.model_copy(update={"translation": cfg.translation.model_copy(update={"model": model})})

    try:
        series, chapter = _resolve_chapter(cfg, target)
        _process_one(
            cfg, series, chapter,
            engine_name=engine or cfg.translation.engine,
            force=force, debug_boxes=debug_boxes, dry_run=dry_run,
        )
    except (ChapterNotFoundError, UnknownEngineError, TranslationError) as error:
        typer.secho(str(error), fg=typer.colors.RED)
        raise typer.Exit(code=1) from error

    save_library(cfg, build_library(cfg))


@app.command(name="process-all")
def process_all(
    series: str = typer.Argument(None, help="Nome da serie; vazio processa a biblioteca inteira"),
    engine: str = typer.Option(None, "--engine", "-e"),
    force: bool = typer.Option(False, "--force"),
    debug_boxes: bool = typer.Option(False, "--debug-boxes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Processa todos os capitulos; os ja processados e inalterados sao no-op."""
    _configure_logging(verbose)
    cfg = _load()
    engine_name = engine or cfg.translation.engine

    pairs = [pair for pair in discover_chapters(cfg) if series is None or pair[0] == series]
    if not pairs:
        typer.secho(f"nenhum capitulo encontrado em {cfg.library_dir}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    failures = 0
    for chapter_series, chapter in pairs:
        try:
            _process_one(
                cfg, chapter_series, chapter,
                engine_name=engine_name, force=force, debug_boxes=debug_boxes, dry_run=dry_run,
            )
        except (ChapterNotFoundError, UnknownEngineError, TranslationError) as error:
            failures += 1
            typer.secho(f"{chapter_series}/{chapter}: {error}", fg=typer.colors.RED)

    save_library(cfg, build_library(cfg))
    if failures:
        typer.secho(f"{failures} capitulo(s) falharam", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command(name="build-library")
def build_library_command() -> None:
    """Regenera output/library.json a partir do que ja existe em disco."""
    cfg = _load()
    path = save_library(cfg, build_library(cfg))
    typer.secho(f"escrito {path}", fg=typer.colors.GREEN)


@app.command(name="setup-free")
def setup_free() -> None:
    """Baixa o pacote de idioma do motor `free` (roda uma vez)."""
    cfg = _load()
    from .engines.argos import install_language_package

    source, target = cfg.translation.source_lang, cfg.translation.target_lang
    typer.echo(f"baixando pacote Argos {source}->{target} (~100MB na primeira vez)...")
    try:
        installed = install_language_package(source, target)
    except (ImportError, TranslationError) as error:
        typer.secho(str(error), fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.secho(f"instalado: {installed}", fg=typer.colors.GREEN)


def _lan_addresses() -> list[str]:
    addresses = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        addresses.update(
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass
    return sorted(address for address in addresses if not address.startswith("127."))


@app.command()
def serve(port: int = typer.Option(8000, "--port", "-p")) -> None:
    """Sobe o leitor web servindo a raiz do projeto (imagens + JSONs + PWA)."""
    cfg = _load()
    save_library(cfg, build_library(cfg))

    handler = type(
        "RootHandler",
        (http.server.SimpleHTTPRequestHandler,),
        {"__init__": lambda self, *a, **kw: http.server.SimpleHTTPRequestHandler.__init__(
            self, *a, directory=str(cfg.root), **kw
        )},
    )

    typer.secho(f"leitor:   http://localhost:{port}/reader/", fg=typer.colors.GREEN)
    for address in _lan_addresses():
        typer.echo(f"celular:  http://{address}:{port}/reader/")
    typer.echo("Ctrl+C para parar")

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            typer.echo("\nparado")


def _check(label: str, ok: bool, hint: str = "") -> bool:
    mark = typer.style("OK  ", fg=typer.colors.GREEN) if ok else typer.style("FALTA", fg=typer.colors.RED)
    typer.echo(f"{mark} {label}")
    if not ok and hint:
        typer.echo(f"      {hint}")
    return ok


@app.command()
def doctor() -> None:
    """Verifica tudo que o pipeline precisa e diz o que falta."""
    import os

    cfg = _load()
    typer.echo(f"projeto: {cfg.root}\n")

    checks = [
        _check("config.toml", True),
        _check(
            "binario tesseract",
            shutil.which("tesseract") is not None,
            "sudo apt install tesseract-ocr",
        ),
    ]

    languages: list[str] = []
    if shutil.which("tesseract"):
        try:
            result = subprocess.run(
                ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30, check=False
            )
            languages = result.stdout.split()
        except (OSError, subprocess.SubprocessError):
            languages = []
    checks.append(
        _check(
            f"idioma tesseract '{cfg.ocr.lang}'",
            cfg.ocr.lang in languages,
            f"sudo apt install tesseract-ocr-{cfg.ocr.lang}",
        )
    )

    for module, hint in (("cv2", "pip install -e ."), ("pytesseract", "pip install -e ."), ("anthropic", "pip install -e .")):
        try:
            __import__(module)
            present = True
        except ImportError:
            present = False
        checks.append(_check(f"pacote {module}", present, hint))

    _check(
        "ANTHROPIC_API_KEY (motor claude)",
        bool(os.environ.get("ANTHROPIC_API_KEY")),
        "copie .env.example para .env e preencha a chave",
    )

    try:
        from .engines.argos import language_package_installed

        free_ready = language_package_installed(cfg.translation.source_lang, cfg.translation.target_lang)
    except ImportError:
        free_ready = False
    _check("motor free (Argos en->pt)", free_ready, "pip install -e '.[free]' && mangatl setup-free")

    typer.echo("")
    _check(f"biblioteca em {cfg.library_dir}", cfg.library_dir.is_dir(), f"mkdir -p {cfg.library_dir}")
    chapters = list(discover_chapters(cfg))
    typer.echo(f"      {len(chapters)} capitulo(s) encontrado(s)")

    if not all(checks):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
