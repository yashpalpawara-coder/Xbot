from __future__ import annotations


def analysis_prompt(post: dict, discussion: list[dict]) -> str:
    lines = [
        "Analyse this public X post for someone deciding whether a thoughtful reply is useful.",
        "Return one JSON object and no other text.",
        "Keys: main_topic, key_message, audience, discussion_context, engagement_quality, opportunity, risks, reply_angle, relevance.",
        "relevance is an integer from 0 to 100.",
        "Use empty strings when a point is unknown. Do not invent metrics or personal facts.",
        "",
        f"Author: {post.get('author') or ''} @{post.get('handle') or ''}",
        f"Post: {post.get('text') or ''}",
        f"Views: {post.get('latest_views')}",
        f"Likes: {post.get('latest_likes')}",
        f"Replies: {post.get('latest_replies')}",
        f"Reposts: {post.get('latest_reposts')}",
    ]
    if discussion:
        lines.append("Visible replies:")
        for item in discussion[:6]:
            lines.append(f"- @{item.get('handle') or 'unknown'}: {item.get('text') or ''}")
    return "\n".join(lines)


def comments_prompt(post: dict, analysis: dict | None, *, allow_promotion: bool, product_name: str, product_url: str, product_pitch: str = "") -> str:
    lines = [
        "You are helping write a thoughtful X reply.",
        "Write three possible replies to the original post.",
        "Return one JSON object and no other text.",
        'Keys: should_reply (boolean), reason (string), natural, insightful, question.',
        "natural is conversational. insightful adds one concrete observation. question asks one answerable question.",
        "Stay on the post. Do not invent personal experience, results, or sources.",
        "Do not use generic praise. Avoid repeating the same opening.",
        "Each reply must stay within 280 characters. A URL counts as 23 characters.",
        "If the post is a poor fit, set should_reply to false, explain why, and leave the three replies empty.",
        "",
        f"Post by @{post.get('handle') or ''}: {post.get('text') or ''}",
    ]
    if analysis:
        lines.append(f"Analysis: {analysis.get('key_message') or ''} Reply angle: {analysis.get('reply_angle') or ''}")
    if allow_promotion:
        lines.append(
            f"A short mention of {product_name} is allowed only when one saved fact fits the author's actual point. "
            f"The link {product_url} is optional. Do not end every reply with it. If it does not fit, mention nothing."
        )
        facts = product_pitch.strip()
        if facts:
            lines.append(f"Use only these facts. Do not invent features:\n{facts[:2000]}")
        else:
            lines.append(f"Use only {product_name} at {product_url}. Do not invent features.")
    else:
        lines.append("Do not mention a product, a link, or a brand.")
    return "\n".join(lines)


def product_prompt(product_name: str, pitch: str, marketing_url: str) -> str:
    """First ChatGPT message. A full product analysis. This does not write a post."""
    del pitch
    name = product_name.strip() or "the product"
    url = marketing_url.strip()
    return (
        f"Analyze this product.\n"
        f"Name: {name}\n"
        f"URL: {url}\n"
        "Read the page. Write the full analysis in this reply. Do not say you will read it later.\n"
        "Cover these sections, in this order:\n"
        "What it is\n"
        "Features\n"
        "Target users\n"
        "Audience type\n"
        f"What to use when promoting {name}, including the name and this URL: {url}\n"
        "Do not write an X post."
    )


def revise_analysis_prompt(change: str, *, name: str, url: str, previous: str) -> str:
    """A later message in the same chat. The owner's note replaces the analysis."""
    return (
        f"The product is {name.strip() or 'the product'}. URL: {url.strip()}\n"
        "Current analysis:\n"
        f"{previous.strip()[:6000]}\n\n"
        f"Change to apply: {change.strip()}\n"
        "Write the full analysis again, not a note about the change. Keep these sections:\n"
        "What it is\n"
        "Features\n"
        "Target users\n"
        "Audience type\n"
        f"What to use when promoting it, including the name and this URL: {url.strip()}\n"
        "Do not write an X post."
    )


def original_post_prompt(product_name: str, marketing_url: str, note: str = "") -> str:
    """Second ChatGPT message. The post, using details already in the chat."""
    name = product_name.strip() or "the product"
    extra = f"\n{note.strip()}\n" if note.strip() else ""
    return (
        "Using only the product details already in this chat, write one original X post.\n"
        "This is the account's own post, not a reply under someone else's post.\n"
        f"You are {name}. Speak as {name} in first person. \"I\" means {name}. "
        f"Never write as a user of {name}.\n"
        "One idea only. Make it motivational, like a short poem.\n"
        "Each body line is one short line a poet would keep. Four to six lines. No paragraphs. No blank lines in the body.\n"
        "Name the product once, in first person. One emoji, on the last body line only.\n"
        "After the body, put two or three hashtags on their own line.\n"
        f"The last line is this URL exactly once, and nowhere else: {marketing_url}\n"
        "No @mentions.\n"
        f"{extra}"
        "Put only the finished post in one code block, so the copy box contains the post and nothing else."
    )


def rewrite_post_prompt(product_name: str, marketing_url: str, problem: str) -> str:
    name = product_name.strip() or "the product"
    return (
        f"That was not a post ({problem}).\n"
        f"Write one original X post now, as {name}. One idea. Each line short, like a poem. One emoji. Hashtags on their own line. "
        f"The last line is {marketing_url}.\n"
        "Put only the finished post in one code block."
    )


def regenerate_prompt(post: dict, variant: str, current: str, *, allow_promotion: bool) -> str:
    promo = "A product mention is allowed only when it fits." if allow_promotion else "Do not mention a product or a link."
    return "\n".join(
        [
            "Rewrite one X reply.",
            "Return one JSON object and no other text.",
            f'Keys: "{variant}" only, containing the new reply.',
            "Keep it specific to the post and different from the current wording.",
            "Stay within 280 characters. Do not invent personal experience.",
            promo,
            "",
            f"Post: {post.get('text') or ''}",
            f"Current {variant} reply: {current}",
        ]
    )
