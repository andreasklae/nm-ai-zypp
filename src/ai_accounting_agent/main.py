from __future__ import annotations

import secrets
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status

from ai_accounting_agent import gemini
from ai_accounting_agent.agent import AgentTaskError, execute_agent
from ai_accounting_agent.schemas import PreparedAttachment, SolveRequest, SolveResponse
from ai_accounting_agent.telemetry import build_attachment_log, log_event


app = FastAPI(
    title="Tripletex Competition Agent",
    version="0.1.0",
    description="Cloud Run-ready Tripletex competition agent.",
)


def _validate_api_key(authorization: str | None, x_api_key: str | None) -> bool:
    import os

    expected = os.environ.get("AI_ACCOUNTING_AGENT_API_KEY")
    if not expected:
        return True

    if x_api_key and secrets.compare_digest(x_api_key, expected):
        return True

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token and secrets.compare_digest(token, expected):
            return True

    return False


@app.post("/solve", tags=["solve"])
async def solve(
    request: SolveRequest,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> SolveResponse:
    if not _validate_api_key(authorization, x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )

    run_id = uuid4().hex
    start = time.perf_counter()
    attachments: list[PreparedAttachment] = []
    attachment_logs: list[dict[str, Any]] = []

    for file in request.files:
        data = file.decoded_bytes()
        attachments.append(
            PreparedAttachment(
                filename=file.filename,
                mime_type=file.mime_type,
                data=data,
            )
        )
        attachment_logs.append(build_attachment_log(filename=file.filename, mime_type=file.mime_type, data=data))

    log_event(
        "request_received",
        run_id=run_id,
        path="/solve",
        auth_present=authorization is not None or x_api_key is not None,
        prompt=request.prompt,
        file_count=len(attachment_logs),
        files=attachment_logs,
        tripletex_base_url=str(request.tripletex_credentials.base_url),
        model=gemini.DEFAULT_GEMINI_MODEL,
    )

    try:
        result = await execute_agent(
            request=request,
            attachments=attachments,
            model=gemini.DEFAULT_GEMINI_MODEL,
            run_id=run_id,
        )
    except AgentTaskError as exc:
        duration_ms = round((time.perf_counter() - start) * 1000)
        response.status_code = status.HTTP_200_OK
        log_event(
            "task_error",
            severity="ERROR",
            run_id=run_id,
            path="/solve",
            model=exc.model,
            duration_ms=duration_ms,
            prompt=request.prompt,
            file_count=len(attachment_logs),
            files=attachment_logs,
            error_type=exc.error_type,
            error_message=exc.error_message,
            graceful_completion=True,
            agent_message_count=len(exc.messages),
        )
        log_event(
            "task_complete",
            run_id=run_id,
            path="/solve",
            model=exc.model,
            status=200,
            duration_ms=duration_ms,
            prompt=request.prompt,
            agent_output=exc.output,
            file_count=len(attachment_logs),
            files=attachment_logs,
            agent_message_count=len(exc.messages),
            usage=exc.usage,
            completed_with_recovery=True,
            recovery_error_type=exc.error_type,
            recovery_error_message=exc.error_message,
        )
        return SolveResponse()
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000)
        log_event(
            "task_error",
            severity="ERROR",
            run_id=run_id,
            path="/solve",
            model=gemini.DEFAULT_GEMINI_MODEL,
            duration_ms=duration_ms,
            prompt=request.prompt,
            file_count=len(attachment_logs),
            files=attachment_logs,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise

    duration_ms = round((time.perf_counter() - start) * 1000)
    response.status_code = status.HTTP_200_OK
    log_event(
        "task_complete",
        run_id=run_id,
        path="/solve",
        model=result.model,
        status=200,
        duration_ms=duration_ms,
        prompt=request.prompt,
        agent_output=result.output,
        file_count=len(attachment_logs),
        files=attachment_logs,
        agent_message_count=len(result.messages),
        usage=result.usage,
    )

    return SolveResponse()
