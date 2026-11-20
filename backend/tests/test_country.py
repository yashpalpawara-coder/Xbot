from app.country import country_from_about, country_from_place


def test_account_based_in_names_the_country() -> None:
    assert country_from_about("Joined March 2020\nAccount based in India") == "India"
    assert country_from_about("Account based in the United States") == "United States"
    assert country_from_about("Account based in: United Kingdom") == "United Kingdom"


def test_a_place_counts_only_when_it_names_a_country() -> None:
    assert country_from_place("London, United Kingdom") == "United Kingdom"
    assert country_from_place("California, USA") == "United States"
    assert country_from_place("London") == ""
    assert country_from_place("日本語") == ""
    assert country_from_about("lang:ja") == ""
