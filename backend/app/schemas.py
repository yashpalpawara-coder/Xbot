from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchIn(BaseModel):
    topic: str = ""
    keywords: str = ""
    hashtags: str = ""
    handles: str = ""
    url: str = ""
    since: str = ""
    until: str = ""
    min_views: int | None = Field(default=None, ge=0)
    min_likes: int | None = Field(default=None, ge=0)
    min_replies: int | None = Field(default=None, ge=0)
    language: str = ""
    sort: Literal["relevant", "likes", "views", "recent"] = "relevant"
    limit: int = Field(default=20, ge=1, le=40)


class CommentEdit(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class CommentGenerate(BaseModel):
    allow_promotion: bool | None = None


class ApproveIn(BaseModel):
    text: str | None = Field(default=None, max_length=500)


class PublishIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80)


class ProductAnalyzeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=300)


class ProductReviseIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class GrokFindIn(BaseModel):
    count: int = Field(default=20, ge=1, le=100)


class ProductVerifyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=300)
    detail: str = Field(min_length=20, max_length=8000)
    for_finder: bool = False


class DailyPostIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class DailyApproveIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class SavedIn(BaseModel):
    item_type: Literal["post", "creator", "search", "comment"]
    item_id: str = Field(min_length=8, max_length=80)
    tags: list[str] = []


class SettingsIn(BaseModel):
    product_name: str = Field(default="Ted", min_length=1, max_length=80)
    product_url: str = "https://tedcoachai.com"
    product_pitch: str = Field(default="", max_length=2000)
    allow_promotion: bool = False
    search_limit: int = Field(default=20, ge=1, le=40)
    min_views: int | None = Field(default=None, ge=0)
    min_likes: int | None = Field(default=None, ge=0)
    x_username: str = Field(default="", max_length=120)
    x_password: str = Field(default="", max_length=200)


class AnalysisBody(BaseModel):
    main_topic: str
    key_message: str
    audience: str
    discussion_context: str
    engagement_quality: str
    opportunity: str
    risks: str
    reply_angle: str
    relevance: int = Field(ge=0, le=100)


class CommentBody(BaseModel):
    natural: str = ""
    insightful: str = ""
    question: str = ""
    should_reply: bool = True
    reason: str = ""
