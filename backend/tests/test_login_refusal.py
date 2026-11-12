from app.browser_session import x_login_refusal


def test_login_limit_is_reported() -> None:
    payload = "begin_login errors We’ve temporarily limited your login. Please try again later."
    assert "temporarily limited" in x_login_refusal(payload)


def test_login_block_is_reported() -> None:
    payload = "Sorry, you are not allowed to log in at this time."
    assert "not allowed to log in" in x_login_refusal(payload)


def test_successful_login_payload_is_empty() -> None:
    assert x_login_refusal("page onboarding section signup") == ""
