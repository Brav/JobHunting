"""Job identity for de-duplication: the same title at the same company is the same job."""

import re

_NON_WORD = re.compile(r"[\W_]+")


def _norm(text: str) -> str:
    return _NON_WORD.sub(" ", text.casefold()).strip()


def match_key(title: str, company: str) -> str:
    return f"{_norm(title)}|{_norm(company)}"
