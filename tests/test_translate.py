from types import SimpleNamespace

import deepl
import pytest

from jobhunt.translate import (BAD_KEY_MESSAGE, NO_KEY_MESSAGE, QUOTA_MESSAGE, DeepLTranslator, NoTranslator,
                               TranslationError, TranslationQuotaExceeded)


class FakeClient:
    def __init__(self, codes=("DE", "EN-US", "PL"), fail=None, fail_lookup=None):
        self.codes, self.fail, self.fail_lookup, self.calls = codes, fail, fail_lookup, []

    def get_source_languages(self):
        if self.fail_lookup:
            raise self.fail_lookup
        return [SimpleNamespace(code=c) for c in self.codes]

    def translate_text(self, text, **kw):
        self.calls.append((text, kw))
        if self.fail:
            raise self.fail
        return SimpleNamespace(text=f"EN:{text}", detected_source_lang="DE")


def test_deepl_translates_supported_language():
    client = FakeClient()
    assert DeepLTranslator("k:fx", client=client).translate("Hallo Welt", "de") == "EN:Hallo Welt"
    assert client.calls == [("Hallo Welt", {"target_lang": "EN-US", "preserve_formatting": True})]


def test_deepl_unsupported_language_makes_no_request():
    client = FakeClient()
    with pytest.raises(TranslationError, match="DeepL can't translate Serbian/Croatian"):
        DeepLTranslator("k:fx", client=client).translate("Тражимо", "sr")
    assert client.calls == []


def test_deepl_quota_and_other_errors():
    quota = DeepLTranslator("k:fx", client=FakeClient(fail=deepl.QuotaExceededException("quota")))
    with pytest.raises(TranslationQuotaExceeded, match=QUOTA_MESSAGE):
        quota.translate("Hallo", "de")
    other = DeepLTranslator("k:fx", client=FakeClient(fail=deepl.DeepLException("boom")))
    with pytest.raises(TranslationError, match="DeepL error: boom"):
        other.translate("Hallo", "de")


def test_deepl_bad_key():
    bad = DeepLTranslator("k:fx", client=FakeClient(fail_lookup=deepl.AuthorizationException("forbidden")))
    with pytest.raises(TranslationError, match="DeepL rejected the key") as exc:
        bad.translate("Hallo", "de")
    assert str(exc.value) == BAD_KEY_MESSAGE and not isinstance(exc.value, TranslationQuotaExceeded)


def test_no_translator():
    with pytest.raises(TranslationError, match="Add a DeepL key"):
        NoTranslator().translate("Hallo", "de")
    assert NO_KEY_MESSAGE.startswith("Add a DeepL key")
