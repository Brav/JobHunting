"""Pay from a job's salary box, title or text, as a yearly range in euros so jobs can be compared and filtered.

An amount counts only next to a currency ("€50K", "65.000 €", "PLN 20 000"), so years, phone numbers and headcounts
never read as pay; budgets, bonuses and allowances don't either, and a lone amount needs a word for pay or a "K". Per-month, -day and -hour pay is annualised (×12, ×220 days, ×1760 hours).
"""

import re
from dataclasses import dataclass

# Approximate euro value of one unit, October 2026: a comparison aid, not accounting.
RATES = {"EUR": 1.0, "GBP": 1.17, "USD": 0.92, "CHF": 1.05, "PLN": 0.23, "SEK": 0.087, "DKK": 0.134, "NOK": 0.085,
         "CZK": 0.040, "HUF": 0.0025, "RON": 0.20, "BGN": 0.51, "RSD": 0.0085, "CAD": 0.67, "AUD": 0.60}
_SYMBOLS = {"€": "EUR", "£": "GBP", "$": "USD", "zł": "PLN", "Kč": "CZK", "lei": "RON", "din": "RSD",
            "euro": "EUR", "euros": "EUR", "Euro": "EUR", "Euros": "EUR"}
_CUR = (r"(?:€|£|\$|\b(?:" + "|".join(RATES) + r")\b|(?<!\w)(?:zł|Kč|lei|din|[Ee]uros?)(?!\w))")
_NUM = r"\d{1,3}(?:[.,   ]\d{3})+(?:[.,]\d{1,2})?(?!\d)|\d+(?:[.,]\d{1,2})?(?!\d)"
AMOUNT = re.compile(rf"(?P<pre>{_CUR})?\s?(?P<num>{_NUM})(?P<k>\s?[kK](?![A-Za-z]))?(?:\s?(?P<post>{_CUR}))?")
RANGE_JOIN = re.compile(r"^\s*(?:/\s*[A-Za-z.]+\s*)?(?:-|–|—|to|bis|à)\s*$")
BIG = re.compile(r"^\s?(?:M\b|m\b|mn\b|bn\b|million|billion|Mio)")  # "$40M raised" is funding, not pay
PERIODS = [
    ("hour", re.compile(r"/\s?(?:h|hr|hour)\b|\bper\s+hour\b|\bhourly\b|\b(?:pro\s+)?Stunde\b|\bpar\s+heure\b|\bpor\s+hora\b", re.I)),
    ("day", re.compile(r"/\s?(?:d|day)\b|\bper\s+day\b|\bday\s+rate\b|\bdaily\b|\bTagessatz\b|\bpro\s+Tag\b|\bpar\s+jour\b|"
                       r"\bal\s+giorno\b|\bpor\s+d[ií]a\b|\bper\s+dag\b|\bdziennie\b", re.I)),
    ("month", re.compile(r"/\s?(?:mo|month|mies\.?|mc)\b|\bper\s+month\b|\bmonthly\b|\bmonatlich\b|\bpro\s+Monat\b|"
                         r"\bpar\s+mois\b|\bal\s+mese\b|\bmensual\b|\bper\s+maand\b|\bmiesi[eę]cznie\b|\bmjese[cč]no\b|"
                         r"\bmesiac\w*|\bmesa[čc]ne\b|\bm[ěe]s[íi][cč]\w*", re.I)),
    ("year", re.compile(r"/\s?(?:yr|year|y|a)\b|\bper\s+(?:year|annum)\b|\bp\.\s?a\.|\bannual(?:ly)?\b|\byearly\b|"
                        r"\bjährlich\b|\bpro\s+Jahr\b|\bpar\s+an\b|\bannuo\b|\banual\b|\bper\s+jaar\b|\brocznie\b", re.I)),
]
TIMES = {"hour": 1760, "day": 220, "month": 12, "year": 1}
# Money that isn't pay: "€500 home office budget", "Referral bonus of €1,000", "1.000 € Weiterbildungsbudget".
PERK = re.compile(r"(?:budget|bonus|allowance|stipend|voucher|relocation|equipment|learning|training|referral|"
                  r"reimburse|per\s+diem|zuschuss|prämie|pr[aä]mie)", re.I)
# A small lone amount with no period ("€2,500") counts only next to a word for pay ("Salary", "brutto", "RAL").
PAY = re.compile(r"\b(?:salar(?:y|ies)|pay|paid|compensation|remuneration|wages?|base|OTE|package|gross|net|brutto|"
                 r"netto|neto|bruto|verg[üu]tung|lohn|salaire|r[ée]mun[ée]ration|sueldo|salario|stipendio|RAL|"
                 r"retribuci[oó]n|retribuzione|wynagrodzenie|plata|zarada|pla[cć]a|rate)\b|gehalt", re.I)
_CLAUSE = re.compile(r"[!?\n;•]|\.\s")
_ADDITION = re.compile(r"\+|\bplus\b|\band\b|&|,|\(|\)|\|", re.I)  # "€60k + Bonus": the bonus is on top


def _next_words(text: str, n: int = 4) -> str:
    return " ".join(_ADDITION.split(_CLAUSE.split(text)[0])[0].split()[:n])


def _previous_words(text: str, n: int = 4) -> str:
    return " ".join(_ADDITION.split(_CLAUSE.split(text)[-1])[-1].split()[-n:])


def _year_like(m: re.Match) -> bool:
    return not m.group("k") and re.fullmatch(r"(?:19|20)\d\d", m.group("num")) is not None


@dataclass(frozen=True)
class Salary:
    min_eur: int
    max_eur: int
    period: str
    currency: str
    text: str  # as written in the job


def _number(text: str) -> float:
    text = text.replace(" ", " ").replace(" ", " ")
    grouped = re.fullmatch(r"(\d{1,3}(?:[., ]\d{3})+)(?:[.,](\d{1,2}))?", text)
    if grouped:
        return float(re.sub(r"[., ]", "", grouped.group(1)))
    return float(text.replace(",", "."))


def _currency(m: re.Match) -> str | None:
    token = m.group("pre") or m.group("post")
    return (_SYMBOLS.get(token) or (token if token in RATES else None)) if token else None


def parse_salary(text: str | None) -> Salary | None:
    """The first pay amount or range in the text, annualised in euros; None if there is none."""
    if not text:
        return None
    amounts = [m for m in AMOUNT.finditer(text) if not BIG.match(text[m.end():])]
    i = 0
    while i < len(amounts):
        first, last = amounts[i], amounts[i]
        if i + 1 < len(amounts) and RANGE_JOIN.match(text[first.end():amounts[i + 1].start()]):
            last = amounts[i + 1]
        currency = _currency(first) or _currency(last)
        if currency is None:
            i += 1
            continue
        low, high = _number(first.group("num")), _number(last.group("num"))
        if last.group("k"):
            high *= 1000
            low *= 1000 if (first.group("k") or low < 1000) else 1
        elif first.group("k"):
            low *= 1000
        low, high = sorted((low, high))
        tail = text[last.end():last.end() + 40]
        head = text[max(0, first.start() - 20):first.start()]
        period = next((name for name, pattern in PERIODS if pattern.search(tail) or pattern.search(head)), None)
        # Words around the amount, up to the next or previous amount and within its sentence or bullet.
        j = amounts.index(last)
        after = text[last.end():amounts[j + 1].start() if j + 1 < len(amounts) else len(text)]
        before = text[amounts[i - 1].end() if i else 0:first.start()]
        lone, says_pay = last is first, PAY.search(before[-60:]) or PAY.search(after[:40])
        euros = high * RATES[currency]
        if (PERK.search(_next_words(after)) or PERK.search(_previous_words(before)) or after.startswith("(k)")
                or (period is None and not says_pay and ((_year_like(first) and _year_like(last))
                                                         or (lone and not first.group("k") and euros < 15000)))):
            i += 1 if lone else 2
            continue
        if period is None:  # no word for it: judge by size
            period = "year" if euros >= 15000 else "month" if euros >= 1000 else "day" if euros >= 100 else "hour"
        factor = TIMES[period] * RATES[currency]
        found = Salary(int(round(low * factor, -2)), int(round(high * factor, -2)), period, currency,
                       " ".join(text[first.start():last.end()].split()))
        if 5000 <= found.max_eur <= 1_000_000:
            return found
        i += 2 if last is not first else 1
    return None


def short(min_eur: int | None, max_eur: int | None) -> str:
    """"€50–60k" (yearly, in euros) for lists."""
    if not max_eur:
        return ""
    low, high = round((min_eur or max_eur) / 1000), round(max_eur / 1000)
    return f"€{high}k" if low == high else f"€{low}–{high}k"
