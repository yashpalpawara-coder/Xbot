from app.country import country_from_about, country_from_place
from app.services import rows_in_country


def test_account_based_in_names_the_country() -> None:
    assert country_from_about("Joined March 2020\nAccount based in India") == "India"
    assert country_from_about("Account based in the United States") == "United States"
    assert country_from_about("Account based in: United Kingdom") == "United Kingdom"


def test_a_selected_country_keeps_only_those_posts() -> None:
    rows = [
        {"country": "India", "status": "saved"},
        {"country": "", "status": "drafted"},
        {"country": "United States", "status": "drafted"},
    ]
    assert [row["country"] for row in rows_in_country(rows, "India")] == ["India"]
    assert [row["country"] for row in rows_in_country(rows, "Unknown")] == [""]
    assert len(rows_in_country(rows, "")) == 3
    assert len(rows_in_country(rows, "All")) == 3


def test_a_place_counts_only_when_it_names_a_country() -> None:
    assert country_from_place("London, United Kingdom") == "United Kingdom"
    assert country_from_place("California, USA") == "United States"
    assert country_from_place("London") == ""
    assert country_from_place("日本語") == ""
    assert country_from_about("lang:ja") == ""
