from app.reply_rules import reply_problems, reply_prompt, taken_reply


def test_reply_uses_the_same_chat_prompt() -> None:
    post = "If you have ADHD, important tasks get pushed aside when novelty takes over."
    message = reply_prompt(post, "https://x.com/Helios_Movement/status/2118251689379533184", name="Ted", url="https://tedcoachai.com")
    assert message.startswith("Now analyze this X post: https://x.com/Helios_Movement/status/2118251689379533184")
    assert f"The post says: {post}" in message
    assert "already extracted in this same chat" in message
    assert "I\" means Ted" in message
    assert "I use Ted" in message
    assert message.endswith("Put only the finished reply in one code block, so the copy box contains the reply and nothing else.")
    assert "https://tedcoachai.com" in message
    assert "Mac app" not in message
    good = "When novelty and urgency take over, important tasks can get pushed aside. I'm Ted, a little Mac app that helps you pick three tasks each morning. 🌱 https://tedcoachai.com"
    other = "Shipping the API gets easier when the checklist is already there. I'm North, and I keep the release list in one place. 🌱 https://north.example"
    assert reply_problems(other, name="North", url="https://north.example", post_text="Ship the API.") == []
    assert reply_problems(good, name="Ted", url="https://tedcoachai.com", post_text=post) == []
    user_voice = "I use Ted every morning on my Mac. 🌱 https://tedcoachai.com"
    assert any("user" in item for item in reply_problems(user_voice, name="Ted", url="https://tedcoachai.com", post_text="Mac"))
    tagged = good + " #Focus"
    assert any("hashtag" in item for item in reply_problems(tagged, name="Ted", url="https://tedcoachai.com", post_text="Mac"))
    assert taken_reply("Sure\n```\nHello Ted on Mac 🌿 https://tedcoachai.com\n```") == "Hello Ted on Mac 🌿 https://tedcoachai.com"
    labeled = "Plain text Notch timers are a great use of that Mac space! ⏱ I'm Ted. https://tedcoachai.com"
    assert reply_problems(taken_reply(labeled), name="Ted", url="https://tedcoachai.com", post_text="Mac notch") == []
