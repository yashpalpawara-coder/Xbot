from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.errors import AppError
from app.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def create_app() -> FastAPI:
    app = FastAPI(title="XBot")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(AppError)
    def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    def invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]["msg"] if exc.errors() else "Check the form."
        return JSONResponse(status_code=422, content={"error": {"code": "invalid", "message": first}})

    return app


app = create_app()
