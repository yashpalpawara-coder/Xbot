from app.reply_rules import reply_problems, reply_prompt, taken_reply


def test_reply_uses_the_same_chat_prompt() -> None:
    post = "If you have ADHD, important tasks get pushed aside when novelty takes over."
    message = reply_prompt(post, "https://x.com/Helios_Movement/status/2118251689379533184", name="Ted", url="https://tedcoachai.com")
    assert message.startswith("Now analyze this X post: https://x.com/Helios_Movement/status/2118251689379533184")
    assert f"The post says: {post}" in message
    assert "already extracted in this same chat" in message
    assert "Do not analyze the product again" in message
    assert "I\" means Ted" in message
    assert "I use Ted" in message
    assert "interesting if the account name is hidden" in message
    assert "Do not add a link" in message
    assert message.endswith("Put only the finished reply in one code block, so the copy box contains the reply and nothing else.")
    assert "https://tedcoachai.com" in message
    assert "Mac app" not in message
    good = "When novelty takes over, the important task is the one that gets pushed aside. The fix is a smaller next step, not more motivation."
    pitched = "Download Ted now and fix your morning. https://tedcoachai.com"
    assert reply_problems(good, name="Ted", url="https://tedcoachai.com", post_text=post) == []
    assert any("download" in item for item in reply_problems(pitched, name="Ted", url="https://tedcoachai.com", post_text=post))
    user_voice = "I use Ted every morning when novelty takes over the important task."
    assert any("user" in item for item in reply_problems(user_voice, name="Ted", url="https://tedcoachai.com", post_text="Mac"))
    tagged = good + " #Focus"
    assert any("hashtag" in item for item in reply_problems(tagged, name="Ted", url="https://tedcoachai.com", post_text="Mac"))
    saved = reply_prompt(post, "https://x.com/Helios_Movement/status/2118251689379533184", name="Ted", url="https://tedcoachai.com", analysis="Ted is a Mac app for three morning tasks.")
    assert saved.startswith("Now analyze this X post:")
    assert "Do not analyze the product again" in saved
    assert "already saved" in saved
    assert "Ted is a Mac app for three morning tasks." in saved
    assert "already extracted in this same chat" not in saved
    assert taken_reply("Sure\n```\nThe notch is useful when the next task stays visible.\n```") == "The notch is useful when the next task stays visible."
    labeled = "Plain text The notch keeps the next task visible after the novelty fades."
    assert reply_problems(taken_reply(labeled), name="Ted", url="https://tedcoachai.com", post_text="Mac notch") == []
