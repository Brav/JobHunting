import pytest

from jobhunt.places import REGIONS, place_name, place_of, places_in


@pytest.mark.parametrize("location, place", [
    ("Madrid, Community of Madrid, Spain", "ES"), ("UK", "GB"), ("London, England, UK", "GB"),
    ("Czech Republic", "CZ"), ("Greater Madrid Metropolitan Area", "ES"), ("Frankfurt Rhine-Main Metropolitan Area", "DE"),
    ("Greater Dublin", "IE"), ("Namur Metropolitan Area", "BE"), ("Arnhem-Nijmegen Region", "NL"),
    ("Belgrade, Serbia", "RS"), ("European Union", "EU"), ("EMEA", "EMEA"), ("Europe", "EUROPE"),
    ("Remote", None), ("", None), (None, None)])
def test_place_of(location, place):
    assert place_of(location) == place


def test_places_in():
    assert places_in("Portugal") == ["PT"]
    assert places_in("the EU or the UK") == ["EU", "GB"]
    assert places_in("Poland, Serbia and Romania") == ["PL", "RS", "RO"]
    assert places_in("the Western Balkans") == ["BALKANS"]
    assert places_in("our office") == []


def test_regions_and_names():
    assert {"DE", "HR", "NO", "IS", "LI", "CH"} <= REGIONS["EU"]
    assert "GB" not in REGIONS["EU"] and "RS" not in REGIONS["EU"]
    assert "RS" in REGIONS["BALKANS"] and "RS" in REGIONS["EUROPE"] and "RS" in REGIONS["EMEA"]
    assert [place_name(c) for c in ("GB", "PT", "EU", "EUROPE")] == ["the UK", "Portugal", "the EU", "Europe"]
