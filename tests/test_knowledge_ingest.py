from core.text_utils import fingerprint, normalize_text, split_into_paragraphs


def test_normalize_text_collapses_whitespace_and_lowercases():
    assert normalize_text("  Hello   World \n\n") == "hello world"


def test_fingerprint_stable_for_equivalent_text():
    a = fingerprint("Hello   World")
    b = fingerprint(" hello world ")
    assert a == b


def test_split_into_paragraphs_splits_and_overlaps():
    text = "A" * 50 + "\n\n" + "B" * 5000
    parts = split_into_paragraphs(text, max_len=1000, overlap=100)
    assert parts[0] == "A" * 50
    assert len(parts) > 2
    assert all(1 <= len(p) <= 1000 for p in parts[1:])

