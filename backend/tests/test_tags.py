"""Coverage for `app.auth.tags.normalize_tags` — every historical `rbac_tags` shape it
still has to accept, per its own docstring."""

from app.auth.tags import normalize_tags


def test_none_becomes_empty_list():
    assert normalize_tags(None) == []


def test_comma_separated_string_is_split():
    assert normalize_tags("a, b") == ["a", "b"]


def test_list_of_strings_passes_through_sorted():
    assert normalize_tags(["b", "a"]) == ["a", "b"]


def test_bool_valued_dict_keeps_just_the_key():
    assert normalize_tags({"legal-team": True}) == ["legal-team"]


def test_non_bool_valued_dict_collapses_to_key_colon_value():
    assert normalize_tags({"confidentiality": "internal"}) == ["confidentiality:internal"]


def test_duplicates_and_whitespace_are_deduped_and_stripped():
    assert normalize_tags(["a", " a ", "b", "a"]) == ["a", "b"]


def test_blank_entries_are_dropped():
    assert normalize_tags(["a", "", "  ", "b"]) == ["a", "b"]
