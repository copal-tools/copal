"""Unit tests for the slug pipeline in `copalpm.pm`.

These lock in the transliteration step that prevents Unicode-named projects
(Greek, Cyrillic, accented Latin, CJK, etc.) from collapsing to empty or
symbol-only slugs — the bug that produced `-40-140526` on the CopalVX server
when the user entered `Κατάρρευση τιμών έως -40%`. See `copalpm/CLAUDE.md`
gotcha #14 for the underlying principle: letters get replaced, symbols may
be truncated.
"""

import pytest

from copalpm.pm import slug_title, make_slug


# ── slug_title (UPPERCASE, used by the project ID) ────────────────────────────

class TestSlugTitleTransliteration:
    def test_greek_input_produces_latin_slug(self):
        # The bug: previously produced '-40' (Greek stripped, symbol survived).
        result = slug_title("Κατάρρευση τιμών έως -40%")
        assert result.startswith("K"), f"Greek romanization expected, got {result!r}"
        assert "40" in result, f"Numeric part should survive, got {result!r}"
        assert not result.startswith("-"), "Leading hyphen would re-trigger argparse bug"

    def test_accented_latin_folds_to_ascii(self):
        assert slug_title("café résumé") == "CAFE-RESUME"

    def test_cyrillic_input_produces_latin_slug(self):
        result = slug_title("Привет мир")
        assert result and result.isascii(), f"Expected ASCII output, got {result!r}"
        assert "-" in result, "Spaces should become hyphens"

    def test_mixed_unicode_and_ascii(self):
        # User typed: "Project Φοίνικας 2025"
        result = slug_title("Project Φοίνικας 2025")
        assert "PROJECT" in result and "2025" in result
        assert result.isascii()


class TestSlugTitleEdgeCases:
    def test_emoji_only_yields_empty(self):
        # Caller (InitScreen._do_create) is expected to reject empty slugs at submit.
        assert slug_title("🎉🎉🎉") == ""

    def test_symbols_only_yields_empty(self):
        assert slug_title("%%%") == ""
        assert slug_title("---") == ""
        assert slug_title("___") == ""

    def test_whitespace_only_yields_empty(self):
        assert slug_title("   ") == ""
        assert slug_title("\t\n  ") == ""

    def test_empty_input(self):
        assert slug_title("") == ""

    def test_none_input_returns_empty(self):
        # `_to_ascii(s or "")` defensively coerces None → "" so the slug
        # pipeline never crashes on a missing name. Empty result is still
        # caught by the InitScreen empty-slug guard before any folder gets created.
        assert slug_title(None) == ""  # type: ignore[arg-type]

    def test_leading_and_trailing_dashes_stripped(self):
        # The defensive `.strip("-_")` keeps degenerate inputs from triggering
        # the `parse_known_args` flag-shifting bug downstream.
        assert slug_title("---Hello---") == "HELLO"
        assert slug_title("___World___") == "WORLD"
        assert slug_title("-Mix-Of-Dashes-") == "MIX-OF-DASHES"


class TestSlugTitleAsciiRegression:
    """Ensure ASCII inputs are not perturbed by the new transliteration step."""

    def test_plain_ascii_unchanged(self):
        assert slug_title("Hello World") == "HELLO-WORLD"

    def test_hyphens_and_underscores_preserved(self):
        assert slug_title("My-Project_v2") == "MY-PROJECT_V2"

    def test_digits_only(self):
        assert slug_title("2025") == "2025"

    def test_multiple_spaces_collapse_to_single_hyphen(self):
        assert slug_title("A   B   C") == "A-B-C"


# ── make_slug (lowercase, used by project.yaml `slug` field) ──────────────────

class TestMakeSlug:
    def test_greek_input_produces_lowercase_latin(self):
        result = make_slug("Κατάρρευση τιμών έως -40%")
        assert result and not result.startswith("-")
        assert result == result.lower()
        assert "40" in result

    def test_accented_latin(self):
        assert make_slug("café résumé") == "cafe-resume"

    def test_cyrillic(self):
        result = make_slug("Привет мир")
        assert result and result.isascii()
        assert result == result.lower()

    def test_emoji_only(self):
        assert make_slug("🎉") == ""

    def test_dashes_only_yields_empty(self):
        assert make_slug("---") == ""

    def test_ascii_regression(self):
        assert make_slug("Hello World") == "hello-world"

    def test_underscore_stripped_by_existing_regex(self):
        # make_slug's existing regex `[^a-z0-9\-]+` strips underscores. Not
        # something we changed — locked in to catch accidental regex edits.
        assert make_slug("My_Project") == "myproject"
