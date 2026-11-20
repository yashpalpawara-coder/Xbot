from app.reply_rules import comment_notes, engagement_count, reply_gate, reply_problems, reply_prompt, taken_reply


def test_reply_uses_the_same_chat_prompt() -> None:
    post = "If you have ADHD, important tasks get pushed aside when novelty takes over."
    message = reply_prompt(post, "https://x.com/Helios_Movement/status/2118251689379533184", name="Ted", url="https://tedcoachai.com")
    assert message.startswith("Now analyze this X post: https://x.com/Helios_Movement/status/2118251689379533184")
    assert f"The post says: {post}" in message
    assert "already extracted in this same chat" in message
    assert "Do not analyze the product again" in message
    assert "I\" means Ted" in message
    assert "I use Ted" in message
    assert "the way a person types a reply on X" in message
    assert "interesting if the account name is hidden" in message
    assert "do not add a link" in message
    assert "SKIP:" in message
    assert "same language" in message
    assert message.endswith("Put only the finished reply in one code block, so the copy box contains the reply and nothing else.")
    assert "https://tedcoachai.com" in message
    assert "Mac app" not in message
    good = "When novelty takes over, the important task is the one that gets pushed aside. The fix is a smaller next step, not more motivation."
    pitched = "Download Ted now and fix your morning. https://tedcoachai.com"
    assert reply_problems(good, name="Ted", url="https://tedcoachai.com", post_text=post) == []
    stiff = "It's worth noting that novelty pushes the important task aside — the smaller step is what holds."
    assert any("spoken" in item for item in reply_problems(stiff, name="Ted", url="https://tedcoachai.com", post_text=post))
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
    assert "comments already on that post" not in message
    notes = comment_notes(
        [
            {"text": "Short take.", "likes": 1, "replies": 0, "views": 10},
            {"text": "A longer comment people actually replied to, with one concrete step.", "likes": 40, "replies": 6, "views": 900},
        ]
    )
    longer = "A longer comment people actually replied to, with one concrete step."
    assert notes.index("1 likes") < notes.index("40 likes")
    assert "Do not imitate" in notes
    assert f"{len(longer)} characters" in notes
    styled = reply_prompt(post, "https://x.com/Helios_Movement/status/2118251689379533184", name="Ted", url="https://tedcoachai.com", comments=[{"text": "A longer comment people actually replied to.", "likes": 40, "replies": 6, "views": 900}])
    assert "Do not copy their wording." in styled
    assert "An emoji is optional." in styled
    assert reply_gate(2400, 8) == ""
    assert "500" in reply_gate(500, 8)
    assert "500" in reply_gate(None, 8)
    assert reply_gate(900, 4) == ""
    assert "500" in reply_gate(12, 0)
    assert reply_gate(120, 10, likes=600) == ""
    assert engagement_count(2, 6) == 6
    assert taken_reply("Sure\n```\nThe notch is useful when the next task stays visible.\n```") == "The notch is useful when the next task stays visible."
    labeled = "Plain text The notch keeps the next task visible after the novelty fades."
    assert reply_problems(taken_reply(labeled), name="Ted", url="https://tedcoachai.com", post_text="Mac notch") == []
