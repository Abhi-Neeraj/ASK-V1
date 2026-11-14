"""One error shape for the whole API:

    { "error": { "code": "snake_case_code", "message": "Human readable.", "details": ... } }

The web client turns this into messages and field errors. Internal errors never leak details.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("ask.api")


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"That {what} was not found.")


def forbidden(message: str = "You do not have permission to do that.") -> ApiError:
    return ApiError(403, "forbidden", message)


def _envelope(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in {"body", "query", "path", "header"}]
    return ".".join(parts) or "request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _envelope(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details: dict[str, str] = {}
        for error in exc.errors():
            field = _field_name(tuple(error.get("loc", ())))
            message = str(error.get("msg", "Invalid value")).removeprefix("Value error, ")
            details.setdefault(field, message)
        return JSONResponse(
            _envelope("validation_error", "Some fields are not valid.", details), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthenticated", 403: "forbidden"}.get(
            exc.status_code, "http_error"
        )
        message = "That resource was not found." if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(_envelope(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the type and where it happened, never the message: it can contain request values, file paths or
        # database rows. (`exc_info=exc` would print the message as part of the traceback.)
        frames = "".join(traceback.format_tb(exc.__traceback__))
        logger.error("unhandled error: %s\n%s", type(exc).__name__, frames)
        return JSONResponse(_envelope("internal_error", "Something went wrong on our side."), status_code=500)
