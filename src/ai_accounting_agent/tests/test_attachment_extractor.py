from __future__ import annotations

import asyncio

from pydantic_ai import BinaryContent

from ai_accounting_agent.attachment_extractor import (
    ExtractedAttachmentPayload,
    ExtractedField,
    extract_attachments,
)
from ai_accounting_agent.schemas import PreparedAttachment


def test_extract_attachments_decodes_text_attachments_without_subagent() -> None:
    report = asyncio.run(
        extract_attachments(
            run_id="run-text",
            task_prompt="Read the attachment.",
            attachments=[
                PreparedAttachment(
                    filename="invoice.md",
                    mime_type="text/markdown",
                    data=b"Invoice Number: INV-123\nCustomer: Example AS\n",
                )
            ],
            model="fake-model",
        )
    )

    assert report.extractor_model == "fake-model"
    assert len(report.attachments) == 1
    attachment = report.attachments[0]
    assert attachment.extraction_method == "text_decode"
    assert "INV-123" in attachment.extracted_text
    assert attachment.document_type == "text"


def test_extract_attachments_uses_subagent_for_pdf(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeRunResult:
        output = ExtractedAttachmentPayload(
            document_type="supplier_invoice",
            language="no",
            summary="Supplier invoice for Example AS.",
            extracted_text="Invoice Number: INV-555\nCustomer: Example AS\nAmount: 1250,00 NOK",
            key_fields=[
                ExtractedField(name="invoice_number", value="INV-555", evidence="Invoice Number: INV-555"),
                ExtractedField(name="customer_name", value="Example AS", evidence="Customer: Example AS"),
            ],
        )

        def usage(self):
            return {"total_tokens": 21}

    class FakeAgent:
        async def run(self, content):
            captured["content"] = content
            return FakeRunResult()

    monkeypatch.setattr(
        "ai_accounting_agent.attachment_extractor._build_extractor_agent",
        lambda model="gemini-2.5-flash": FakeAgent(),
    )

    report = asyncio.run(
        extract_attachments(
            run_id="run-pdf",
            task_prompt="Read the invoice attachment and extract the invoice number.",
            attachments=[
                PreparedAttachment(
                    filename="invoice.pdf",
                    mime_type="application/pdf",
                    data=b"%PDF-1.4",
                )
            ],
            model="gemini-2.5-flash",
        )
    )

    assert len(report.attachments) == 1
    attachment = report.attachments[0]
    assert attachment.extraction_method == "subagent"
    assert attachment.document_type == "supplier_invoice"
    assert attachment.key_fields[0].value == "INV-555"
    assert isinstance(captured["content"], list)
    assert isinstance(captured["content"][1], BinaryContent)
