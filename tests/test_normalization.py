from core.text_utils import normalize_entity_name


def test_basic_entity_normalization():
    assert normalize_entity_name("Graphiti") == "graphiti"
    assert normalize_entity_name("  Data  ") is None
    assert normalize_entity_name("Project") is None
    assert normalize_entity_name("Test Project") == "test project"


def test_punctuation_case_and_cyrillic():
    assert normalize_entity_name("Hello, World!") == "hello world"
    assert normalize_entity_name("User-Name") == "username"
    assert normalize_entity_name("«Цитата»") == "цитата"
    assert normalize_entity_name("Сергей") == "сергей"
    assert normalize_entity_name("Ёлка") == "елка"
    assert normalize_entity_name("Ещё") == "еще"


def test_stop_words_and_short_names_are_rejected():
    for word in ["System", "Data", "Memory", "Graph", "AI", "Model", "User", "Chat"]:
        assert normalize_entity_name(word) is None
        assert normalize_entity_name(word.lower()) is None

    assert normalize_entity_name("A") is None
    assert normalize_entity_name("No") is None
    assert normalize_entity_name("") is None
