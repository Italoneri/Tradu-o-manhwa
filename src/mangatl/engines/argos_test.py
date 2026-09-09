from __future__ import annotations

import pytest

from .argos import protect_terms, restore_terms

GLOSSARY = {"Sect Master": "Mestre da Seita", "Qi": "Qi", "Lin Feng": "Lin Feng"}


def round_trip(text: str, glossary: dict[str, str] = GLOSSARY) -> str:
    protected, replacements = protect_terms(text, glossary)
    return restore_terms(protected, replacements)


def test_returns_text_unchanged_without_a_glossary():
    protected, replacements = protect_terms("THE SECT MASTER IS HERE", {})

    assert protected == "THE SECT MASTER IS HERE"
    assert replacements == {}


def test_hides_glossary_terms_from_the_translator():
    protected, _ = protect_terms("THE SECT MASTER IS HERE", GLOSSARY)

    assert "SECT MASTER" not in protected


def test_restores_the_glossary_translation():
    assert round_trip("THE SECT MASTER IS HERE") == "THE Mestre da Seita IS HERE"


def test_prefers_the_longest_matching_term():
    assert round_trip("SECT MASTER") == "Mestre da Seita"


def test_matches_terms_regardless_of_case():
    assert round_trip("the sect master") == "the Mestre da Seita"


def test_leaves_partial_word_matches_alone():
    assert round_trip("QUALITY") == "QUALITY"


def test_protects_several_distinct_terms_in_one_line():
    assert round_trip("LIN FENG GATHERS QI") == "Lin Feng GATHERS Qi"


def test_survives_a_translator_that_rewrites_around_the_marker():
    protected, replacements = protect_terms("LIN FENG IS HERE", GLOSSARY)
    as_if_translated = protected.replace("IS HERE", "esta aqui")

    assert restore_terms(as_if_translated, replacements) == "Lin Feng esta aqui"


def test_leaves_unknown_markers_untouched():
    assert restore_terms("MTLTERM9X esta aqui", {}) == "MTLTERM9X esta aqui"


@pytest.mark.parametrize("text", ["", "   ", "NO TERMS HERE"])
def test_returns_no_replacements_when_nothing_matches(text: str):
    _, replacements = protect_terms(text, GLOSSARY)

    assert replacements == {}
