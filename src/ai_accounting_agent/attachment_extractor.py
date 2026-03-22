from __future__ import annotations

import os
import time
from typing import Literal

from pydantic import Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelHTTPError

from ai_accounting_agent import gemini
from ai_accounting_agent.schemas import PreparedAttachment, StrictBaseModel
from ai_accounting_agent.telemetry import log_event

ATTACHMENT_EXTRACTOR_MODEL = os.environ.get("ATTACHMENT_EXTRACTOR_MODEL", "gemini-2.5-flash")
TEXT_ATTACHMENT_MIME_TYPES = {
    "application/csv",
    "application/json",
    "application/xml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/xml",
}

ATTACHMENT_EXTRACTION_SYSTEM_PROMPT = """\
You are an attachment extraction specialist for accounting workflows.

You will receive exactly one attachment together with the main task prompt for context.

Your job:
1. Identify the document type.
2. Extract the attachment contents faithfully.
3. Surface accounting-relevant fields without inventing anything.
4. Be explicit about uncertainty or unreadable sections.

Rules:
- Never invent values that are not visible in the attachment.
- Preserve important numbers, dates, invoice references, names, amounts, and account-like identifiers exactly.
- If the document is an image or scan, perform OCR and state uncertainty in warnings when needed.
- Keep the summary concise and factual.
- Put long verbatim content in extracted_text, not in summary.
"""


class ExtractedField(StrictBaseModel):
    name: str = Field(min_length=1)
    value: str = Field(min_length=1)
    evidence: str | None = None


class ExtractedAttachmentPayload(StrictBaseModel):
    document_type: str = Field(min_length=1)
    language: str | None = None
    summary: str = Field(min_length=1)
    extracted_text: str = Field(default="")
    key_fields: list[ExtractedField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractedAttachment(StrictBaseModel):
    filename: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    extraction_method: Literal["subagent", "text_decode", "failed"]
    document_type: str = Field(min_length=1)
    language: str | None = None
    summary: str = Field(min_length=1)
    extracted_text: str = Field(default="")
    key_fields: list[ExtractedField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AttachmentExtractionReport(StrictBaseModel):
    summary: str = Field(min_length=1)
    extractor_model: str | None = None
    attachments: list[ExtractedAttachment] = Field(default_factory=list)


def _is_text_attachment(attachment: PreparedAttachment) -> bool:
    return attachment.mime_type.startswith("text/") or attachment.mime_type in TEXT_ATTACHMENT_MIME_TYPES


def _decode_text_attachment(attachment: PreparedAttachment) -> ExtractedAttachment:
    extracted_text = attachment.data.decode("utf-8", errors="replace").strip()
    summary = extracted_text.splitlines()[0].strip() if extracted_text else "Text attachment with no visible content."
    return ExtractedAttachment(
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        extraction_method="text_decode",
        document_type="text",
        summary=summary[:400] or "Text attachment decoded directly.",
        extracted_text=extracted_text,
    )


def _build_extractor_agent(model: str = ATTACHMENT_EXTRACTOR_MODEL) -> Agent[None]:
    return Agent(
        gemini.build_google_model(model=model),
        instructions=ATTACHMENT_EXTRACTION_SYSTEM_PROMPT,
        output_type=ExtractedAttachmentPayload,
        model_settings=gemini.attachment_extractor_model_settings(),
        name="attachment-extractor-agent",
    )


def _build_report_summary(attachments: list[ExtractedAttachment]) -> str:
    if not attachments:
        return "No attachments were provided."
    if len(attachments) == 1:
        attachment = attachments[0]
        return (
            f"Extracted 1 attachment ({attachment.filename}) via {attachment.extraction_method}. "
            f"Detected type: {attachment.document_type}."
        )
    return f"Extracted {len(attachments)} attachments for the main agent."


async def extract_attachments(
    *,
    run_id: str,
    task_prompt: str,
    attachments: list[PreparedAttachment],
    model: str = ATTACHMENT_EXTRACTOR_MODEL,
) -> AttachmentExtractionReport:
    if not attachments:
        return AttachmentExtractionReport(
            summary="No attachments were provided.",
            extractor_model=model,
            attachments=[],
        )

    agent: Agent[None] | None = None
    extracted: list[ExtractedAttachment] = []

    for attachment in attachments:
        if _is_text_attachment(attachment):
            decoded = _decode_text_attachment(attachment)
            extracted.append(decoded)
            log_event(
                "attachment_extraction_direct_decode",
                run_id=run_id,
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
            )
            continue

        if agent is None:
            agent = _build_extractor_agent(model=model)

        started = time.perf_counter()
        try:
            result = await agent.run(
                [
                    (
                        "Extract the attached file for the main accounting agent.\n\n"
                        f"Main task prompt:\n{task_prompt.strip()}\n\n"
                        f"Attachment filename: {attachment.filename}\n"
                        f"Attachment MIME type: {attachment.mime_type}\n"
                    ),
                    BinaryContent(data=attachment.data, media_type=attachment.mime_type),
                ]
            )
        except ModelHTTPError as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            extracted.append(
                ExtractedAttachment(
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    extraction_method="failed",
                    document_type="unknown",
                    summary="Attachment extraction failed in the sub-agent.",
                    warnings=[str(exc)],
                )
            )
            log_event(
                "attachment_extraction_subagent_error",
                severity="WARNING",
                run_id=run_id,
                filename=attachment.filename,
                mime_type=attachment.mime_type,
                model=model,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            continue

        duration_ms = round((time.perf_counter() - started) * 1000)
        payload = result.output
        extracted_attachment = ExtractedAttachment(
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            extraction_method="subagent",
            **payload.model_dump(),
        )
        extracted.append(extracted_attachment)
        log_event(
            "attachment_extraction_subagent_result",
            run_id=run_id,
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            model=model,
            duration_ms=duration_ms,
            usage=result.usage(),
            document_type=payload.document_type,
            summary=payload.summary,
            warning_count=len(payload.warnings),
        )

    return AttachmentExtractionReport(
        summary=_build_report_summary(extracted),
        extractor_model=model,
        attachments=extracted,
    )
