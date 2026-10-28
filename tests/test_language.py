from jobhunt.language import detect_language, language_name


def test_detect_language():
    assert detect_language("Wir suchen einen erfahrenen PHP-Entwickler für unser Team in Berlin.") == "de"
    assert detect_language("Тражимо искусног PHP програмера за рад у нашем тиму у Београду.") == "sr"
    assert detect_language("Szukamy doświadczonego programisty PHP do naszego zespołu w Warszawie.") == "pl"
    assert detect_language("We are looking for an experienced PHP developer to join our team.") == "en"
    assert detect_language("   ") is None


def test_language_name():
    assert [language_name(c) for c in ("de", "pl", "sr", "hr", "bs", "xx")] == [
        "German", "Polish", "Serbian/Croatian", "Serbian/Croatian", "Serbian/Croatian", "XX"]
