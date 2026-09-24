"""Unit tests for the voice resolve lexicon (PRD §5 rules, es + en)."""

from __future__ import annotations

import pytest

from herdr_brain.approval_lexicon import (
    APPROVE_WORDS,
    MAX_UTTERANCE_CHARS,
    OUTCOME_APPROVE,
    OUTCOME_REJECT,
    OUTCOME_REPLACE_INTENT,
    OUTCOME_UNKNOWN,
    REJECT_WORDS,
    REPLACE_WORDS,
    resolve_utterance,
)


class TestOutcomeContract:
    def test_outcome_strings_are_stable(self):
        # T4 maps these exact strings onto store decisions; do not rename.
        assert OUTCOME_APPROVE == "approve"
        assert OUTCOME_REJECT == "reject"
        assert OUTCOME_REPLACE_INTENT == "replace_intent"
        assert OUTCOME_UNKNOWN == "unknown"

    def test_short_utterance_limit_is_24(self):
        assert MAX_UTTERANCE_CHARS == 24


class TestExactTriggers:
    @pytest.mark.parametrize("word", APPROVE_WORDS)
    def test_approve_word_exact(self, word):
        assert resolve_utterance(word) == OUTCOME_APPROVE

    @pytest.mark.parametrize("word", REJECT_WORDS)
    def test_reject_word_exact(self, word):
        assert resolve_utterance(word) == OUTCOME_REJECT

    @pytest.mark.parametrize("word", REPLACE_WORDS)
    def test_replace_word_exact(self, word):
        assert resolve_utterance(word) == OUTCOME_REPLACE_INTENT

    @pytest.mark.parametrize(
        "word, outcome",
        [
            ("SÍ", OUTCOME_APPROVE),
            ("No", OUTCOME_REJECT),
            ("CANCELAR", OUTCOME_REJECT),
            ("Cambia", OUTCOME_REPLACE_INTENT),
            ("Go Ahead", OUTCOME_APPROVE),
        ],
    )
    def test_exact_match_is_case_insensitive(self, word, outcome):
        assert resolve_utterance(word) == outcome


class TestStartswithMatches:
    @pytest.mark.parametrize(
        "utterance, expected",
        [
            ("sí, envíalo ya", OUTCOME_APPROVE),        # via "sí" prefix
            ("dale, manda eso", OUTCOME_APPROVE),       # via "dale" prefix
            ("go ahead and send it", OUTCOME_APPROVE),  # en, 20 chars
            ("cancela todo", OUTCOME_REJECT),           # via "cancela" prefix
            ("no lo envíes porfa", OUTCOME_REJECT),     # via "no lo envíes"
            ("cambia el texto porfa", OUTCOME_REPLACE_INTENT),  # "cambia"
        ],
    )
    def test_short_prefixed_utterance_matches(self, utterance, expected):
        assert resolve_utterance(utterance) == expected

    def test_surrounding_whitespace_is_trimmed(self):
        assert resolve_utterance("  sí  ") == OUTCOME_APPROVE


class TestShortUtteranceRule:
    # "dale " is 5 chars; padding reaches the exact boundary length.
    def test_boundary_24_chars_still_matches(self):
        utterance = "dale " + "a" * (MAX_UTTERANCE_CHARS - 5)
        assert len(utterance) == 24
        assert resolve_utterance(utterance) == OUTCOME_APPROVE

    def test_boundary_25_chars_does_not_match(self):
        utterance = "dale " + "a" * (MAX_UTTERANCE_CHARS - 4)
        assert len(utterance) == 25
        assert resolve_utterance(utterance) == OUTCOME_UNKNOWN

    def test_length_guard_applies_to_reject_too(self):
        short = "no " + "a" * (MAX_UTTERANCE_CHARS - 3)
        assert len(short) == 24
        assert resolve_utterance(short) == OUTCOME_REJECT
        long = "no " + "a" * (MAX_UTTERANCE_CHARS - 2)
        assert len(long) == 25
        assert resolve_utterance(long) == OUTCOME_UNKNOWN

    def test_replace_intent_guarded_by_length(self):
        assert len("cambia el texto por favor") == 25
        assert resolve_utterance("cambia el texto por favor") == OUTCOME_UNKNOWN
        assert len("cambia el texto porfa") == 21
        assert resolve_utterance("cambia el texto porfa") == OUTCOME_REPLACE_INTENT


class TestNoSéAndNoLoEnvíes:
    @pytest.mark.parametrize("utterance", ["no sé", "no se", "NO SÉ"])
    def test_no_sé_resolves_to_reject(self, utterance):
        # PRD §5: "no sé" resolves to reject (safe direction).
        assert resolve_utterance(utterance) == OUTCOME_REJECT

    @pytest.mark.parametrize("utterance", ["no lo envíes", "no lo envies"])
    def test_no_lo_envíes_is_reject(self, utterance):
        assert resolve_utterance(utterance) == OUTCOME_REJECT


class TestLongSentencesContainingNo:
    def test_long_sentence_starting_with_no_is_unknown(self):
        utterance = "no creo que sea buena idea enviarlo ahora mismo"
        assert resolve_utterance(utterance) == OUTCOME_UNKNOWN

    @pytest.mark.parametrize(
        "utterance",
        [
            "estoy seguro de que no lo quiero enviar",
            "por favor no lo envíes todavía",
        ],
    )
    def test_mid_sentence_no_is_unknown(self, utterance):
        assert resolve_utterance(utterance) == OUTCOME_UNKNOWN


class TestUnknownInputs:
    @pytest.mark.parametrize("utterance", ["", "   ", "\t\n  "])
    def test_empty_or_whitespace_is_unknown(self, utterance):
        assert resolve_utterance(utterance) == OUTCOME_UNKNOWN

    def test_unmatched_short_utterance_is_unknown(self):
        assert resolve_utterance("hola qué tal") == OUTCOME_UNKNOWN


class TestAccentAndCaseNormalization:
    @pytest.mark.parametrize(
        "utterance",
        ["SÍ", "EnVíAlO", "envialo", "Mándalo", "mandalo"],
    )
    def test_approve_accent_and_case_variants(self, utterance):
        assert resolve_utterance(utterance) == OUTCOME_APPROVE

    @pytest.mark.parametrize("utterance", ["CancelAr", "NO LO ENVÍES"])
    def test_reject_accent_and_case_variants(self, utterance):
        assert resolve_utterance(utterance) == OUTCOME_REJECT


class TestLiteralStartswithSemantics:
    """The PRD rule is a literal startswith with no word boundary.

    Short words that merely begin with a trigger DO resolve; the
    24-char guard is the only protection. Locked here so T3/T4 see the
    accepted tradeoff explicitly.
    """

    def test_word_starting_with_approve_trigger(self):
        assert resolve_utterance("sigue") == OUTCOME_APPROVE  # "si" prefix

    def test_word_starting_with_reject_trigger(self):
        assert resolve_utterance("paraguas") == OUTCOME_REJECT  # "para"
