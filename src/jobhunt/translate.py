"""Translation into English with DeepL. The only module that imports deepl."""

from typing import Any, Protocol

import deepl

from jobhunt.language import SERBIAN_CROATIAN, language_name

# Job pages wait for DeepL: retry once, not 5 times with back-off (~30 s), when DeepL is down.
deepl.http_client.max_network_retries = 1

NO_KEY_MESSAGE = "Add a DeepL key in Settings → Translation to translate listings"
QUOTA_MESSAGE = "DeepL's free monthly limit is used up — translations resume next month"
BAD_KEY_MESSAGE = "DeepL rejected the key — check it in Settings → Translation"


class TranslationError(Exception):
    """This text can't be translated (e.g. an unsupported language); worth remembering per job."""


class TranslationUnavailable(TranslationError):
    """The service can't translate right now (no key, bad key, network, DeepL error); not about the text."""


class TranslationQuotaExceeded(TranslationUnavailable):
    """The monthly translation allowance is used up."""


class Translator(Protocol):
    def translate(self, text: str, language: str) -> str: ...


class SwitchableTranslator:
    """The translator the app hands out once; saving or removing a key in Settings swaps what it uses."""

    def __init__(self, inner: Translator):
        self._inner = inner

    def set(self, inner: Translator) -> None:
        self._inner = inner

    def translate(self, text: str, language: str) -> str:
        return self._inner.translate(text, language)


class NoTranslator:
    def translate(self, text: str, language: str) -> str:
        raise TranslationUnavailable(NO_KEY_MESSAGE)


class DeepLTranslator:
    def __init__(self, auth_key: str, client: Any | None = None):
        self._client = client or deepl.DeepLClient(auth_key)
        self._sources: set[str] | None = None

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except deepl.QuotaExceededException as e:
            raise TranslationQuotaExceeded(QUOTA_MESSAGE) from e
        except deepl.AuthorizationException as e:
            raise TranslationUnavailable(BAD_KEY_MESSAGE) from e
        except deepl.DeepLException as e:
            raise TranslationUnavailable(f"DeepL error: {e}") from e

    def _supports(self, language: str) -> bool:
        if self._sources is None:
            languages = self._call(self._client.get_source_languages)
            self._sources = {lang.code.split("-")[0].lower() for lang in languages}
        if language in SERBIAN_CROATIAN:
            return bool(self._sources & SERBIAN_CROATIAN)
        return language in self._sources

    def translate(self, text: str, language: str) -> str:
        if not self._supports(language):
            raise TranslationError(f"DeepL can't translate {language_name(language)}")
        result = self._call(self._client.translate_text, text, target_lang="EN-US", preserve_formatting=True)
        return result.text
