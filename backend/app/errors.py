from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .observability import structured_log


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.headers = headers
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        structured_log(
            "request.error",
            status_code=exc.status_code,
            error_code=exc.code,
            result="error",
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "location": [str(part) for part in item.get("loc", [])],
                "type": str(item.get("type", "validation_error")),
            }
            for item in exc.errors()
        ]
        structured_log(
            "request.validation_error",
            status_code=422,
            error_count=len(details),
            result="error",
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不符合要求。",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, _exc: Exception) -> JSONResponse:
        structured_log(
            "request.unexpected_error",
            level=40,
            status_code=500,
            error_type=type(_exc).__name__,
            result="error",
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务暂时不可用，请稍后重试。",
                    "details": None,
                }
            },
        )
