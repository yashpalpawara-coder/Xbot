from app.browser_session import reply_audience_blocked
from app.reply_rules import comment_notes, engagement_count, ensure_product_link, reply_gate, reply_problems, reply_prompt, taken_reply
from app.validation import weighted_length


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
    assert "still sound human if the account name is hidden" in message
    assert "not like an ad" in message
    assert "include https://tedcoachai.com once" in message
    assert "There is no skip." in message
    assert "Two short sentences" in message
    assert "under 180 characters" in message
    assert "SKIP:" not in message
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
    assert reply_gate(500, 8) == ""
    assert "500" in reply_gate(400, 8)
    assert "500" in reply_gate(None, 8)
    assert reply_gate(900, 4) == ""
    assert "500" in reply_gate(12, 0)
    assert reply_gate(120, 10, likes=600) == ""
    assert engagement_count(2, 6) == 6
    assert taken_reply("Sure\n```\nThe notch is useful when the next task stays visible.\n```") == "The notch is useful when the next task stays visible."
    chrome = (
        "MacBook storage filling up again is frustrating.\n"
        "https://www.zypmi.com/\n"
        "ChatGPT is AI and can make mistakes. Check important info.\n"
        "Latest response\n"
        "High\n"
        "Response complete"
    )
    assert taken_reply(chrome) == "MacBook storage filling up again is frustrating.\nhttps://www.zypmi.com/"
    labeled = "Plain text The notch keeps the next task visible after the novelty fades."
    assert reply_problems(taken_reply(labeled), name="Ted", url="https://tedcoachai.com", post_text="Mac notch") == []


def test_a_solution_reply_gets_the_saved_product_url() -> None:
    link = "https://zypmi.com"
    direct = ensure_product_link(
        "The cache is the part that fills the disk.",
        link,
        "Which app clears Mac storage?",
    )
    assert direct.endswith(link)
    indirect = ensure_product_link(
        "The disk fills up after a few weeks of downloads.\nZypmi keeps the files off the Mac.",
        link,
        "My Mac storage routine keeps filling up.",
    )
    assert indirect.splitlines()[0].endswith("downloads.")
    assert link not in indirect
    plain = ensure_product_link("Nice photo from the hike.", link, "Look at this trail.")
    assert link not in plain
    long_reply = ("The old install is still sitting in System Data and Logic has to reopen. " * 8) + link
    fitted = ensure_product_link(long_reply, link, "Logic keeps crashing because the disk is full.")
    assert weighted_length(fitted) <= 179
    assert fitted.endswith(link)
    assert reply_audience_blocked("Only some accounts can reply.")
    assert reply_audience_blocked("Only people @alex follows can reply")
    assert not reply_audience_blocked("Anyone can reply to this thread about storage.")


def test_a_storage_reply_is_not_forced_into_a_pitch_line() -> None:
    facts = (
        "What it is\nA cloud virtual disk for macOS.\n"
        "Features\nIt moves files worth keeping off the internal Mac disk.\n"
        "Limitations\nIt does not clean caches or delete old builds. It does not increase SSD capacity.\n"
    )
    post = "The scan found 28GB of caches and 26GB of old builds, with a dry run before delete."
    reaction = "28GB of caches is the part you can delete. The builds are the ones worth a second look."
    assert reply_problems(reaction, name="zypmi", url="https://www.zypmi.com/", post_text=post, facts=facts) == []
    pitched = "28GB of caches can go, but those 26GB of old builds are worth keeping on a cloud disk. https://www.zypmi.com/"
    assert reply_problems(pitched, name="zypmi", url="https://www.zypmi.com/", post_text=post, facts=facts) == []
    cleaner = "zypmi can clean those caches and delete the old builds. https://www.zypmi.com/"
    assert any("facts" in item for item in reply_problems(cleaner, name="zypmi", url="https://www.zypmi.com/", post_text=post, facts=facts))
    again = "26GB of old builds is the slow part. Files you still want can sit on a cloud disk. https://www.zypmi.com/"
    assert not any("repeats" in item for item in reply_problems(again, name="zypmi", url="https://www.zypmi.com/", post_text=post, facts=facts, recent=[pitched]))
