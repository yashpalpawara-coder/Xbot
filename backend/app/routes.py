from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.browser_session import remote_click, remote_key, remote_screen, remote_type
from app.errors import AppError
from app.schemas import ApproveIn, CommentEdit, CommentGenerate, DailyApproveIn, DailyPostIn, GrokFindIn, ProductAnalyzeIn, ProductReviseIn, ProductVerifyIn, PublishIn, SavedIn, SearchIn, SettingsIn
from app.state import get_state

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, object]:
    state = get_state()
    return {
        "status": "ok",
        "database": state.store.mode,
        "database_detail": state.store.detail,
        "browser_profile": str(state.settings.profile_path),
    }


@router.get("/sessions/status")
def session_status() -> dict[str, object]:
    return get_state().service.session_status()


@router.post("/sessions/x/check")
def check_x() -> dict[str, object]:
    return get_state().service.check_x()


@router.post("/sessions/chatgpt/check")
def check_chatgpt() -> dict[str, object]:
    return get_state().service.check_chatgpt()


class ScreenInput(BaseModel):
    x: int | None = None
    y: int | None = None
    text: str = Field(default="", max_length=200)
    key: str = ""


@router.get("/sessions/screen")
def session_screen() -> Response:
    png = remote_screen()
    if not png:
        return Response(status_code=204)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/sessions/input")
def session_input(body: ScreenInput) -> dict[str, bool]:
    if body.x is not None and body.y is not None:
        remote_click(body.x, body.y)
    if body.text:
        remote_type(body.text)
    if body.key:
        remote_key(body.key)
    return {"ok": True}


@router.post("/finder/search")
def start_search(body: SearchIn) -> dict[str, object]:
    return get_state().service.start_search(body)


@router.get("/finder/coming")
def list_coming() -> dict[str, object]:
    return {"posts": get_state().service.list_coming()}


@router.post("/finder/grok")
def start_grok_find(body: GrokFindIn | None = None) -> dict[str, object]:
    count = body.count if body is not None else 20
    return get_state().service.start_grok_find(count)


@router.post("/coming/comment")
def start_coming_comments() -> dict[str, object]:
    return get_state().service.start_coming_comments()


@router.get("/finder/posts")
def list_posts(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    return {"posts": get_state().service.list_posts(limit)}


@router.get("/posts/{post_id}")
def read_post(post_id: str) -> dict[str, object]:
    return get_state().service.post_detail(post_id)


@router.post("/posts/{post_id}/analyse")
def analyse(post_id: str) -> dict[str, object]:
    return get_state().service.start_analysis(post_id)


@router.post("/posts/{post_id}/comments")
def generate_comments(post_id: str, body: CommentGenerate | None = None) -> dict[str, object]:
    allow = None if body is None else body.allow_promotion
    return get_state().service.start_comments(post_id, allow)


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: str, body: CommentEdit) -> dict[str, object]:
    return {"comment": get_state().service.edit_comment(comment_id, body.text)}


@router.post("/comments/{comment_id}/regenerate")
def regenerate(comment_id: str) -> dict[str, object]:
    return get_state().service.regenerate(comment_id)


@router.post("/comments/{comment_id}/approve")
def approve(comment_id: str, body: ApproveIn | None = None) -> dict[str, object]:
    text = None if body is None else body.text
    return {"comment": get_state().service.approve(comment_id, text)}


@router.post("/comments/{comment_id}/publish")
def publish(comment_id: str, body: PublishIn) -> dict[str, object]:
    return get_state().service.start_publish(comment_id, body.idempotency_key)


@router.post("/comments/{comment_id}/confirm")
def confirm(comment_id: str) -> dict[str, object]:
    return get_state().service.confirm_publish(comment_id)


@router.get("/analytics/overview")
def overview() -> dict[str, object]:
    return get_state().service.overview()


@router.get("/activity")
def activity() -> dict[str, object]:
    return get_state().service.activity_snapshot()


@router.post("/activity/scan")
def scan_activity() -> dict[str, object]:
    return get_state().service.start_activity()


@router.post("/activity/responses/{response_id}/draft")
def draft_activity(response_id: str) -> dict[str, object]:
    return get_state().service.start_activity_draft(response_id)


@router.get("/post/today")
def today_post() -> dict[str, object]:
    return get_state().service.daily_post_state()


@router.get("/product")
def latest_product() -> dict[str, object]:
    return {"product": get_state().service.latest_product()}


@router.post("/post/details")
def post_details(body: ProductAnalyzeIn) -> dict[str, object]:
    return get_state().service.start_post_details(body.name, body.url)


@router.post("/post/details/revise")
def revise_post_details(body: ProductReviseIn) -> dict[str, object]:
    return get_state().service.start_revise_details(body.text)


@router.post("/post/details/verify")
def verify_post_details(body: ProductVerifyIn) -> dict[str, object]:
    return {"product": get_state().service.verify_product(body.name, body.url, body.detail, body.for_finder)}


@router.post("/post/today")
def write_today_post(body: DailyPostIn) -> dict[str, object]:
    return get_state().service.start_daily_post(body.text)


@router.post("/post/approve")
def approve_today_post(body: DailyApproveIn) -> dict[str, object]:
    return get_state().service.start_publish_daily(body.text)


@router.get("/jobs")
def list_jobs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
    return {"jobs": get_state().jobs.list_recent(limit)}


@router.get("/jobs/{job_id}")
def read_job(job_id: str) -> dict[str, object]:
    state = get_state()
    job = state.jobs.get(job_id) or state.store.get_job(job_id)
    if job is None:
        raise AppError("Job not found.", 404, "not_found")
    if "cancel" in job:
        return state.jobs.public(job)
    return job


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    state = get_state()

    async def stream():
        while True:
            job = state.jobs.get(job_id) or state.store.get_job(job_id)
            if job is None:
                payload = {"error": {"code": "not_found", "message": "Job not found."}}
                yield f"data: {json.dumps(payload)}\n\n"
                return
            if "cancel" in job:
                job = state.jobs.public(job)
            yield f"data: {json.dumps(job)}\n\n"
            if job.get("status") in {"done", "error", "stopped"}:
                return
            await asyncio.sleep(0.8)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, object]:
    return get_state().jobs.stop(job_id)


@router.get("/saved")
def list_saved() -> dict[str, object]:
    return {"items": get_state().service.saved_library()}


@router.post("/saved")
def save_item(body: SavedIn) -> dict[str, object]:
    return {"item": get_state().service.save_item(body.item_type, body.item_id, body.tags)}


@router.delete("/saved/{saved_id}")
def delete_saved(saved_id: str) -> dict[str, str]:
    get_state().store.delete_saved(saved_id)
    return {"status": "deleted"}


@router.get("/settings")
def read_settings() -> dict[str, object]:
    return {"settings": get_state().service.current_settings()}


@router.put("/settings")
def write_settings(body: SettingsIn) -> dict[str, object]:
    return {"settings": get_state().service.save_settings(body)}


@router.get("/export")
def export_data() -> dict[str, object]:
    return get_state().service.export()
