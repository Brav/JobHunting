import pytest

from jobhunt.workplace import details_value, workplace_from


@pytest.mark.parametrize("text, expected", [
    ("Senior PHP Developer (Remote)", "remote"), ("PHP Developer – 100% remote", "remote"),
    ("Backend-Entwickler (m/w/d) Homeoffice", "remote"), ("Développeur PHP – télétravail", "remote"),
    ("Programista PHP – zdalnie", "remote"), ("Hybrid Laravel Engineer", "hybrid"),
    ("Sviluppatore PHP – ibrido", "hybrid"), ("PHP Developer on-site in Vienna", "on-site"),
    ("Entwickler vor Ort in Bonn", "on-site"), ("Senior PHP Developer", None),
])
def test_workplace_keywords(text, expected):
    assert workplace_from(text) == expected


def test_workplace_order_and_fallback():
    assert workplace_from("Hybrid role", "Remote days available") == "hybrid"  # first text decides
    assert workplace_from("PHP Developer", "Berlin", "We work hybrid and remote") == "hybrid"
    assert workplace_from("PHP Developer", "Berlin", is_remote=True) == "remote"
    assert workplace_from(None, "", None) is None


def test_workplace_whole_words():
    assert workplace_from("Remoteness research engineer", "Hybridization lab", "onsiteX GmbH") is None


def test_details_value():
    details = [["Seniority level", "Mid-Senior level"], ["Employment type", "Full-time"]]
    assert details_value(details, "employment type") == "Full-time"
    assert details_value(details, "Industries") is None
