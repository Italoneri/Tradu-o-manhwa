from __future__ import annotations

import json
from pathlib import Path

import pytest

from .config import Config
from .models import (
    PIPELINE_VERSION,
    BBox,
    Chapter,
    ExtractedPage,
    Extraction,
    TranslatedBlock,
    TranslatedPage,
)
from .store import (
    SOURCE_DIRNAME,
    build_library,
    discover_chapters,
    discover_series,
    find_cover,
    image_sha256,
    is_page_current,
    list_page_images,
    load_chapter,
    load_extraction,
    load_glossary,
    save_chapter,
    save_extraction,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(root=tmp_path)


def write_pages(cfg: Config, series: str, chapter: str, names: list[str]) -> list[Path]:
    chapter_dir = cfg.library_dir / series / chapter
    chapter_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = chapter_dir / name
        path.write_bytes(name.encode())
        paths.append(path)
    return paths


def extraction_for(cfg: Config, series: str, chapter: str, images: list[Path]) -> Extraction:
    return Extraction(
        series=series,
        chapter=chapter,
        pipeline_version=PIPELINE_VERSION,
        pages=tuple(
            ExtractedPage(
                index=index,
                image=path.name,
                width=800,
                height=1200,
                image_sha256=image_sha256(path),
            )
            for index, path in enumerate(images, start=1)
        ),
    )


def test_lists_pages_in_natural_not_lexicographic_order(cfg: Config):
    write_pages(cfg, "serie", "001", ["10.jpg", "2.jpg", "1.jpg"])

    names = [path.name for path in list_page_images(cfg.library_dir / "serie" / "001")]

    assert names == ["1.jpg", "2.jpg", "10.jpg"]


def test_ignores_non_image_files(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg", "notas.txt", "002.png"])

    names = [path.name for path in list_page_images(cfg.library_dir / "serie" / "001")]

    assert names == ["001.jpg", "002.png"]


def test_discovers_chapters_that_have_images(cfg: Config):
    write_pages(cfg, "serie-a", "001", ["001.jpg"])
    write_pages(cfg, "serie-a", "002", ["001.jpg"])
    (cfg.library_dir / "serie-b" / "vazio").mkdir(parents=True)

    assert list(discover_chapters(cfg)) == [("serie-a", "001"), ("serie-a", "002")]


def test_round_trips_extraction_through_disk(cfg: Config):
    images = write_pages(cfg, "serie", "001", ["001.jpg"])
    original = extraction_for(cfg, "serie", "001", images)

    save_extraction(cfg, original)

    assert load_extraction(cfg, "serie", "001") == original


def test_treats_unchanged_page_as_current(cfg: Config):
    images = write_pages(cfg, "serie", "001", ["001.jpg"])
    extraction = extraction_for(cfg, "serie", "001", images)

    assert is_page_current(extraction, images[0], PIPELINE_VERSION)


def test_treats_edited_page_as_stale(cfg: Config):
    images = write_pages(cfg, "serie", "001", ["001.jpg"])
    extraction = extraction_for(cfg, "serie", "001", images)
    images[0].write_bytes(b"conteudo diferente")

    assert not is_page_current(extraction, images[0], PIPELINE_VERSION)


def test_treats_every_page_as_stale_after_pipeline_bump(cfg: Config):
    images = write_pages(cfg, "serie", "001", ["001.jpg"])
    extraction = extraction_for(cfg, "serie", "001", images)

    assert not is_page_current(extraction, images[0], PIPELINE_VERSION + 1)


def test_treats_unknown_page_as_stale(cfg: Config):
    images = write_pages(cfg, "serie", "001", ["001.jpg", "002.jpg"])
    extraction = extraction_for(cfg, "serie", "001", images[:1])

    assert not is_page_current(extraction, images[1], PIPELINE_VERSION)


def chapter_for(series: str, chapter: str, engine: str) -> Chapter:
    return Chapter(
        series=series,
        chapter=chapter,
        engine=engine,
        model="claude-sonnet-5" if engine == "claude" else None,
        pipeline_version=PIPELINE_VERSION,
        created_at="2026-09-08T00:00:00+00:00",
        pages=(
            TranslatedPage(
                index=1,
                image="001.jpg",
                width=800,
                height=1200,
                blocks=(
                    TranslatedBlock(
                        id="p001-b01",
                        bbox=BBox(x=10, y=10, w=100, h=50),
                        source_text="HELLO",
                        text="OLA",
                    ),
                ),
            ),
        ),
    )


def test_keeps_one_file_per_engine(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "claude"))
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    assert load_chapter(cfg, "serie", "001", "claude").engine == "claude"
    assert load_chapter(cfg, "serie", "001", "free").engine == "free"


def test_lists_available_engines_per_chapter_in_library(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "claude"))
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    library = build_library(cfg)

    assert library.series[0].chapters[0].engines == ("claude", "free")
    assert library.series[0].chapters[0].page_count == 1


def test_omits_chapters_with_no_translation_from_library(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])

    assert build_library(cfg).series == ()


def test_records_path_bases_so_the_reader_never_guesses(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "claude"))

    library = build_library(cfg)

    assert library.library_base == "library"
    assert library.output_base == "output"


def test_returns_empty_glossary_when_absent(cfg: Config):
    assert load_glossary(cfg, "serie") == {}


def test_loads_glossary_of_a_series(cfg: Config):
    series_dir = cfg.library_dir / "serie"
    series_dir.mkdir(parents=True)
    (series_dir / "glossary.json").write_text(
        json.dumps({"Qi": "Qi", "Sect Master": "Mestre da Seita"}), encoding="utf-8"
    )

    assert load_glossary(cfg, "serie") == {"Qi": "Qi", "Sect Master": "Mestre da Seita"}


def test_rejects_glossary_that_is_not_an_object(cfg: Config):
    series_dir = cfg.library_dir / "serie"
    series_dir.mkdir(parents=True)
    (series_dir / "glossary.json").write_text(json.dumps(["Qi"]), encoding="utf-8")

    with pytest.raises(ValueError, match="objeto JSON"):
        load_glossary(cfg, "serie")


def write_cover(cfg: Config, series: str, chapter: str | None, name: str) -> Path:
    """Capa da serie quando `chapter` e None, capa do capitulo quando nao."""
    parent = cfg.library_dir / series if chapter is None else cfg.library_dir / series / chapter
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / name
    path.write_bytes(name.encode())
    return path


@pytest.mark.parametrize("name", ["cover.jpg", "cover.png", "cover.webp", "COVER.JPG"])
def test_finds_cover_in_any_image_format(cfg: Config, name: str):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    write_cover(cfg, "serie", "001", name)

    found = find_cover(cfg.library_dir / "serie" / "001")

    assert found is not None
    assert found.name == name


def test_keeps_cover_out_of_the_pages(cfg: Config):
    """Senao a capa vira pagina: entra no OCR, na traducao e no meio da leitura."""
    write_pages(cfg, "serie", "001", ["001.jpg", "002.jpg"])
    write_cover(cfg, "serie", "001", "cover.jpg")

    names = [path.name for path in list_page_images(cfg.library_dir / "serie" / "001")]

    assert names == ["001.jpg", "002.jpg"]


def test_finds_no_cover_when_the_chapter_has_none(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])

    assert find_cover(cfg.library_dir / "serie" / "001") is None


def test_ignores_a_cover_that_is_not_an_image(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    write_cover(cfg, "serie", "001", "cover.txt")

    assert find_cover(cfg.library_dir / "serie" / "001") is None


def test_reports_the_chapter_cover_as_a_servable_path(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    write_cover(cfg, "serie", "001", "cover.png")
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    library = build_library(cfg)

    assert library.series[0].chapters[0].cover == "library/serie/001/cover.png"


def test_prefers_the_series_own_cover_over_a_chapters(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    write_cover(cfg, "serie", "001", "cover.jpg")
    write_cover(cfg, "serie", None, "cover.png")
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    library = build_library(cfg)

    assert library.series[0].cover == "library/serie/cover.png"


def test_inherits_the_series_cover_from_the_first_chapter_that_has_one(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    write_pages(cfg, "serie", "002", ["001.jpg"])
    write_cover(cfg, "serie", "002", "cover.jpg")
    save_chapter(cfg, chapter_for("serie", "001", "free"))
    save_chapter(cfg, chapter_for("serie", "002", "free"))

    library = build_library(cfg)

    assert library.series[0].cover == "library/serie/002/cover.jpg"
    assert library.series[0].chapters[0].cover is None


def test_reports_no_cover_when_nothing_has_one(cfg: Config):
    write_pages(cfg, "serie", "001", ["001.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    library = build_library(cfg)

    assert library.series[0].cover is None
    assert library.series[0].chapters[0].cover is None


# ---------- a verdade do disco, que e outra pergunta ----------


def test_lists_a_series_that_has_no_chapter_yet(cfg: Config):
    # E o primeiro estado de toda serie criada pelo painel: a tela precisa lista-la
    # justamente para voce escolher onde subir o primeiro capitulo.
    (cfg.library_dir / "serie-nova").mkdir(parents=True)

    states = discover_series(cfg)

    assert [state.series for state in states] == ["serie-nova"]
    assert states[0].chapters == ()


def test_lists_a_chapter_that_was_sent_but_not_translated(cfg: Config):
    # O estado normal entre o upload e o botao de traduzir.
    write_pages(cfg, "serie", "001", ["1.jpg", "2.jpg"])

    chapter = discover_series(cfg)[0].chapters[0]

    assert chapter.chapter == "001"
    assert chapter.image_count == 2
    assert chapter.engines == ()
    assert chapter.incoming is False


def test_lists_every_engine_a_chapter_was_translated_by(cfg: Config):
    write_pages(cfg, "serie", "001", ["1.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "free"))
    save_chapter(cfg, chapter_for("serie", "001", "claude"))

    assert discover_series(cfg)[0].chapters[0].engines == ("claude", "free")


def test_marks_a_chapter_with_an_open_upload(cfg: Config):
    write_pages(cfg, "serie", "001", ["1.jpg"])
    (cfg.library_dir / "serie" / "001.incoming").mkdir()

    chapter = discover_series(cfg)[0].chapters[0]

    assert chapter.chapter == "001"
    assert chapter.incoming is True


def test_lists_a_chapter_that_only_exists_as_an_interrupted_upload(cfg: Config):
    # Sem isso a tela nao teria como oferecer "continuar upload interrompido".
    (cfg.library_dir / "serie" / "002.incoming").mkdir(parents=True)

    chapter = discover_series(cfg)[0].chapters[0]

    assert chapter.chapter == "002"
    assert chapter.image_count == 0
    assert chapter.incoming is True


def test_keeps_the_archived_capture_from_becoming_a_chapter(cfg: Config):
    # `_source/` e a captura original que o fatiamento guardou, nao um capitulo.
    write_pages(cfg, "serie", "001", ["1.jpg"])
    (cfg.library_dir / "serie" / SOURCE_DIRNAME).mkdir()

    assert [c.chapter for c in discover_series(cfg)[0].chapters] == ["001"]


def test_orders_chapters_naturally_not_lexicographically(cfg: Config):
    for chapter in ("10", "2", "1"):
        write_pages(cfg, "serie", chapter, ["1.jpg"])

    assert [c.chapter for c in discover_series(cfg)[0].chapters] == ["1", "2", "10"]


def test_counts_images_and_not_translated_pages(cfg: Config):
    """Os dois numeros divergem de proposito: 3 capturas viram 155 fatias."""
    write_pages(cfg, "serie", "001", ["1.jpg", "2.jpg", "3.jpg"])
    save_chapter(cfg, chapter_for("serie", "001", "free"))

    state = discover_series(cfg)[0]
    library = build_library(cfg)

    assert state.chapters[0].image_count == 3
    assert library.series[0].chapters[0].page_count == 1


def test_takes_the_display_title_from_series_json(cfg: Config):
    write_pages(cfg, "serie", "001", ["1.jpg"])
    (cfg.library_dir / "serie" / "series.json").write_text(
        json.dumps({"title": "O Titulo Bonito"}), encoding="utf-8"
    )

    assert discover_series(cfg)[0].title == "O Titulo Bonito"


def test_keeps_an_untranslated_series_out_of_the_reader_index(cfg: Config):
    # A mesma pasta responde presente para o painel e ausente para o leitor.
    write_pages(cfg, "serie", "001", ["1.jpg"])

    assert len(discover_series(cfg)) == 1
    assert build_library(cfg).series == ()
