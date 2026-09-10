from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest

from .config import SlicingConfig
from .slicing import (
    SOURCE_DIRNAME,
    _quietest_row,
    frame_margin,
    ink_per_row,
    is_tall,
    plan_cuts,
    quiet_level,
    slice_chapter_in_place,
    slice_image,
    slice_stream,
)

WIDTH = 200
GUTTERS = ((1100, 1150), (1900, 1950))


@pytest.fixture
def cfg() -> SlicingConfig:
    return SlicingConfig(max_height=1000, min_height=200, format="png")


def strip(height: int = 3000, gutters: tuple[tuple[int, int], ...] = GUTTERS) -> np.ndarray:
    """Tira vertical com tinta em toda linha, exceto nas calhas informadas."""
    image = np.full((height, WIDTH), 255, dtype=np.uint8)
    image[:, 50:150] = 0
    for top, bottom in gutters:
        image[top:bottom, :] = 255
    return image


@pytest.mark.parametrize(
    ("name", "width", "height", "expected"),
    [
        ("captura de rolagem", 1004, 29799, True),
        ("pagina de manga comum", 800, 1200, False),
        ("screenshot de tela", 1004, 678, False),
        ("alta mas dentro do limite de altura", 1004, 900, False),
        ("proporcao exatamente no corte", 1000, 3000, True),
    ],
)
def test_recognises_scroll_captures(name: str, width: int, height: int, expected: bool):
    assert is_tall(width, height, SlicingConfig()) is expected, name


def test_counts_ink_per_row():
    counts = ink_per_row(strip(height=200, gutters=((50, 60),)))

    assert counts[0] == 100
    assert counts[55] == 0


def test_ignores_a_capture_frame_when_counting_ink():
    """Uma borda de poucos pixels poe tinta em toda linha e apagaria as calhas."""
    image = strip(height=200, gutters=((50, 60),))
    image[:, :4] = 0

    with_frame = ink_per_row(image)
    without_frame = ink_per_row(image, frame_margin(image.shape[1]))

    assert with_frame[55] == 4
    assert without_frame[55] == 0


def test_cuts_in_the_middle_of_the_widest_gap():
    ink = np.array([5, 5, 0, 0, 0, 0, 5, 0, 0, 5])

    assert _quietest_row(ink, 0, 10, 0) == 4


def test_treats_a_constant_border_as_still_quiet():
    """Pagina real nunca tem linha de tinta zero: borda e marca d'agua veem em todas."""
    ink = np.array([90, 90, 4, 4, 4, 4, 90, 90])

    cut = _quietest_row(ink, 0, 8, quiet_level(ink, 1004))

    assert cut in range(2, 6), "o corte tem de cair dentro da calha, nao sobre a arte"


def test_cuts_at_the_least_inked_row_when_there_is_no_gap():
    ink = np.array([9, 7, 3, 8, 9])

    assert _quietest_row(ink, 0, 5, 0) == 2


def test_keeps_a_short_image_whole(cfg: SlicingConfig):
    assert plan_cuts(strip(height=800), cfg) == [(0, 800)]


def test_cuts_scroll_capture_inside_the_gutters(cfg: SlicingConfig):
    bounds = plan_cuts(strip(), cfg)

    cuts = [top for top, _ in bounds[1:]]
    assert 1125 in cuts and 1925 in cuts


def test_covers_the_whole_image_without_gap_or_overlap(cfg: SlicingConfig):
    height = 3000
    bounds = plan_cuts(strip(height=height), cfg)

    assert bounds[0][0] == 0
    assert bounds[-1][1] == height
    assert all(previous[1] == following[0] for previous, following in pairwise(bounds))


def test_covers_the_whole_image_even_without_any_gutter(cfg: SlicingConfig):
    height = 3000
    bounds = plan_cuts(strip(height=height, gutters=()), cfg)

    assert bounds[0][0] == 0
    assert bounds[-1][1] == height
    assert all(bottom > top for top, bottom in bounds)


def test_never_produces_a_slice_taller_than_max_height(cfg: SlicingConfig):
    assert all(bottom - top <= cfg.max_height for top, bottom in plan_cuts(strip(), cfg))


def test_never_produces_a_slice_taller_than_max_height_without_gutters(cfg: SlicingConfig):
    bounds = plan_cuts(strip(gutters=()), cfg)

    assert all(bottom - top <= cfg.max_height for top, bottom in bounds)


def test_breaks_ties_towards_the_target_height():
    assert _quietest_row(np.full(101, 7), 0, 101, 0) == 50


def test_writes_one_file_per_slice(tmp_path: Path, cfg: SlicingConfig):
    source = tmp_path / "captura.png"
    image = strip()
    cv2.imwrite(str(source), image)

    written = slice_image(source, tmp_path / "saida", cfg)

    assert len(written) == len(plan_cuts(image, cfg))
    assert [path.name for path in written] == [
        f"p{index:04d}.png" for index in range(1, len(written) + 1)
    ]
    assert all(path.is_file() for path in written)


def test_slices_reassemble_into_the_source(tmp_path: Path, cfg: SlicingConfig):
    """Nenhuma linha se perde nem se repete no corte."""
    source = tmp_path / "captura.png"
    image = strip()
    cv2.imwrite(str(source), image)

    written = slice_image(source, tmp_path / "saida", cfg)
    rebuilt = np.vstack([cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in written])

    assert rebuilt.shape == image.shape
    assert np.array_equal(rebuilt, image)


def test_slices_keep_the_source_width(tmp_path: Path, cfg: SlicingConfig):
    source = tmp_path / "captura.png"
    cv2.imwrite(str(source), strip())

    written = slice_image(source, tmp_path / "saida", cfg)

    assert all(cv2.imread(str(path)).shape[1] == WIDTH for path in written)


def test_archives_the_original_after_slicing(tmp_path: Path, cfg: SlicingConfig):
    source = tmp_path / "captura.png"
    cv2.imwrite(str(source), strip())

    slice_chapter_in_place(tmp_path, [source], cfg)

    assert not source.exists()
    assert (tmp_path / SOURCE_DIRNAME / "captura.png").is_file()


def test_leaves_normal_pages_untouched(tmp_path: Path, cfg: SlicingConfig):
    page = tmp_path / "001.png"
    cv2.imwrite(str(page), np.full((900, 800), 255, dtype=np.uint8))

    assert slice_chapter_in_place(tmp_path, [page], cfg) == []
    assert page.is_file()


def test_slicing_an_already_sliced_chapter_does_nothing(tmp_path: Path, cfg: SlicingConfig):
    source = tmp_path / "captura.png"
    cv2.imwrite(str(source), strip())
    written = slice_chapter_in_place(tmp_path, [source], cfg)

    assert slice_chapter_in_place(tmp_path, written, cfg) == []


def test_reports_a_source_it_cannot_decode(tmp_path: Path, cfg: SlicingConfig):
    broken = tmp_path / "quebrada.png"
    broken.write_bytes(b"nao sou um png")

    with pytest.raises(ValueError, match="decodificar"):
        slice_chapter_in_place(tmp_path, [broken], cfg)


def parts(tmp_path: Path, heights: list[int]) -> list[Path]:
    """Captura partida num teto fixo, como um macro de rolagem entrega."""
    written = []
    for index, height in enumerate(heights, start=1):
        path = tmp_path / f"parte_{index:02d}.png"
        cv2.imwrite(str(path), strip(height=height, gutters=()))
        written.append(path)
    return written


def test_numbers_slices_continuously_across_files(tmp_path: Path, cfg: SlicingConfig):
    written = slice_stream(parts(tmp_path, [1500, 1500]), tmp_path / "saida", cfg)

    assert [path.name for path in written] == [
        f"p{index:04d}.png" for index in range(1, len(written) + 1)
    ]


def test_stream_reassembles_into_the_concatenated_source(tmp_path: Path, cfg: SlicingConfig):
    """Nenhuma linha se perde nem se duplica na fronteira entre arquivos."""
    heights = [1500, 1500, 900]
    sources = parts(tmp_path, heights)
    expected = np.vstack([cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in sources])

    written = slice_stream(sources, tmp_path / "saida", cfg)
    rebuilt = np.vstack([cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) for path in written])

    assert rebuilt.shape == expected.shape
    assert np.array_equal(rebuilt, expected)


def test_carries_content_across_a_file_boundary(tmp_path: Path, cfg: SlicingConfig):
    """A fronteira do macro nao pode virar fronteira de fatia.

    Sem o carry, cada arquivo fatiaria isolado e toda fronteira entre arquivos
    seria tambem um corte - exatamente onde o macro ja partiu um balao ao meio.
    """
    sources = parts(tmp_path, [1500, 1500])
    written = slice_stream(sources, tmp_path / "saida", cfg)

    alturas = [cv2.imread(str(path), cv2.IMREAD_GRAYSCALE).shape[0] for path in written]
    fronteiras = set()
    total = 0
    for altura in alturas[:-1]:
        total += altura
        fronteiras.add(total)

    assert 1500 not in fronteiras


def test_flushes_the_carry_when_the_next_part_has_another_width(tmp_path: Path, cfg: SlicingConfig):
    first = tmp_path / "parte_01.png"
    second = tmp_path / "parte_02.png"
    cv2.imwrite(str(first), strip(height=1500, gutters=()))
    cv2.imwrite(str(second), np.full((1500, WIDTH * 2), 255, dtype=np.uint8))

    written = slice_stream([first, second], tmp_path / "saida", cfg)

    assert len(written) > 0
    assert {cv2.imread(str(path), cv2.IMREAD_GRAYSCALE).shape[1] for path in written} == {WIDTH, WIDTH * 2}
