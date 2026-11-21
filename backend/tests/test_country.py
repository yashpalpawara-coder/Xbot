from app.country import country_from_about, country_from_place, shown_country
from app.services import rows_in_country


def test_only_four_countries_stay_named() -> None:
    assert country_from_about("Account based in Canada") == "Canada"
    assert country_from_about("Account based in Ireland") == "Ireland"
    assert country_from_about("Account based in: United Kingdom") == "United Kingdom"
    assert country_from_about("Account based in the United States") == "United States"
    assert country_from_about("Joined March 2020\nAccount based in India") == ""
    assert country_from_about("Account based in Australia") == ""
    assert shown_country("UK") == "United Kingdom"
    assert shown_country("USA") == "United States"
    assert shown_country("India") == "Unknown"
    assert shown_country("") == "Unknown"


def test_a_selected_country_keeps_only_those_posts() -> None:
    rows = [
        {"country": "India", "status": "saved"},
        {"country": "", "status": "drafted"},
        {"country": "United States", "status": "drafted"},
        {"country": "Ireland", "status": "saved"},
    ]
    assert [row["country"] for row in rows_in_country(rows, "United States")] == ["United States"]
    assert [row["country"] for row in rows_in_country(rows, "Ireland")] == ["Ireland"]
    assert [row["country"] for row in rows_in_country(rows, "Unknown")] == ["India", ""]
    assert len(rows_in_country(rows, "")) == 4
    assert len(rows_in_country(rows, "All")) == 4


def test_a_place_counts_only_when_it_names_a_kept_country() -> None:
    assert country_from_place("London, United Kingdom") == "United Kingdom"
    assert country_from_place("California, USA") == "United States"
    assert country_from_place("Dublin, Ireland") == "Ireland"
    assert country_from_place("Toronto, Canada") == "Canada"
    assert country_from_place("Mumbai, India") == ""
    assert country_from_place("London") == ""
    assert country_from_place("日本語") == ""
    assert country_from_about("lang:ja") == ""
