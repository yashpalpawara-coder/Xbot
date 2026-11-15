from app.x_account import same_account


def test_same_account_matches_the_saved_handle() -> None:
    assert same_account("tedcoachhq", "tedcoachhq", "")
    assert same_account("@VimVo60", "someone@email.com", "vimvo60")
    assert not same_account("tedcoachhq", "VimVo60", "VimVo60")
    assert not same_account("tedcoachhq", "vim@example.com", "")
    assert not same_account("", "VimVo60", "VimVo60")
