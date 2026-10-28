"""Offline language detection (Lingua). The only module that imports lingua."""

from functools import cache

from lingua import Language, LanguageDetectorBuilder

# Lingua can't reliably tell these apart in Latin script, so they share one name.
SERBIAN_CROATIAN = frozenset({"sr", "hr", "bs"})


@cache
def _detector():
    return LanguageDetectorBuilder.from_all_languages().build()


@cache
def _names() -> dict[str, str]:
    return {language.iso_code_639_1.name.lower(): language.name.title() for language in Language.all()}


def detect_language(text: str) -> str | None:
    """Lower-case ISO 639-1 code of the text's language, or None if blank or undecided."""
    if not text.strip():
        return None
    language = _detector().detect_language_of(text)
    return language.iso_code_639_1.name.lower() if language else None


def language_name(code: str) -> str:
    if code in SERBIAN_CROATIAN:
        return "Serbian/Croatian"
    return _names().get(code, code.upper())
