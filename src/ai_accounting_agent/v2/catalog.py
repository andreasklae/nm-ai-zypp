import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_accounting_agent.api_index import _format_schema, _tokenize
from ai_accounting_agent.v2.learned_rules import get_learned_rules_for_endpoint

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EndpointRecord:
    method: str
    path: str
    summary: str
    description: str
    tags: list[str]
    schema: dict[str, Any]
    learned_rules: list[str]

    def to_document_text(self) -> str:
        """Text representation used for TF-IDF indexing."""
        parts = [self.method, self.path, self.summary, self.description] + self.tags + self.learned_rules
        return " ".join(filter(None, parts))


class EndpointCatalog:
    """Loads and indexes Tripletex API endpoints from api_index_data.json."""

    def __init__(
        self,
        json_path: str = "api_index_data.json",
        csv_path: str = "v2/tripletex_api_operations.csv",
    ):
        self.records: list[EndpointRecord] = []
        self._tag_keywords: dict[str, set[str]] = {}
        self._load_data(json_path, csv_path)
        self._build_index()

    def _load_data(self, json_path: str, csv_path: str) -> None:
        p_json = Path(__file__).parent.parent / json_path
        p_csv = Path(__file__).parent.parent / csv_path

        csv_metadata = {}
        if p_csv.exists():
            with open(p_csv, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    method = row.get("method", "").upper()
                    path = row.get("path", "")
                    key = f"{method} {path}"
                    csv_metadata[key] = row
        else:
            logger.warning(f"Could not find CSV API operations at {p_csv}. Supplemental documentation will be missing.")

        if not p_json.exists():
            logger.warning(f"Could not find API index JSON at {p_json}. Search will be empty.")
            return

        with open(p_json, encoding="utf-8") as f:
            data = json.load(f)

        for tag, ops in data.items():
            for op in ops:
                method = op.get("method", "").upper()
                path = op.get("path", "")
                summary = op.get("summary", "")
                description = op.get("description", "")
                rules = get_learned_rules_for_endpoint(method, path)

                # Merge in any CSV metadata as extra context in the schema dict
                key = f"{method} {path}"
                csv_row = csv_metadata.get(key, {})
                if csv_row:
                    op["_csv_metadata"] = csv_row

                self.records.append(
                    EndpointRecord(
                        method=method,
                        path=path,
                        summary=summary,
                        description=description,
                        tags=[tag],
                        schema=op,
                        learned_rules=rules,
                    )
                )

    def _build_index(self) -> None:
        self._record_keywords = {}
        for record in self.records:
            keywords = set()
            for tag in record.tags:
                keywords.update(_tokenize(tag))
            keywords.update(_tokenize(record.summary))
            keywords.update(_tokenize(record.description))
            keywords.update(_tokenize(record.path))
            for rule in record.learned_rules:
                keywords.update(_tokenize(rule))

            # Add simple synonyms for common accounting/ERP terms
            synonyms = {
                "paid": ["payment", "settled", "openpost", "receivable", "ledger", "posting", "invoice"],
                "unpaid": ["openpost", "receivable", "ledger", "posting", "invoice", "outstanding"],
                "pay": ["payment", "settled", "ledger", "posting", "invoice"],
                "book": ["voucher", "ledger", "posting"],
                "expense": ["voucher", "ledger", "posting", "supplierinvoice"],
                "fx": ["agio", "disagio", "currency", "exchange", "rate", "difference"],
            }

            expanded_keywords = set(keywords)
            for word in keywords:
                for target, syn_list in synonyms.items():
                    if word == target:
                        expanded_keywords.update(syn_list)

            self._record_keywords[id(record)] = expanded_keywords

    def search(self, query: str, top_k: int = 15) -> list[EndpointRecord]:
        """Search the catalog and return top results."""
        if not self.records:
            return []

        # Expand query tokens too
        synonyms = {
            "paid": ["payment", "settled", "openpost", "receivable", "ledger", "posting", "invoice"],
            "unpaid": ["openpost", "receivable", "ledger", "posting", "invoice", "outstanding"],
            "pay": ["payment", "settled", "ledger", "posting", "invoice"],
            "book": ["voucher", "ledger", "posting"],
            "expense": ["voucher", "ledger", "posting", "supplierinvoice"],
            "fx": ["agio", "disagio", "currency", "exchange", "rate", "difference"],
        }

        base_tokens = _tokenize(query)
        query_tokens = set(base_tokens)
        for word in base_tokens:
            for target, syn_list in synonyms.items():
                if word == target:
                    query_tokens.update(syn_list)

        if not query_tokens:
            return []

        scored_records = []
        for record in self.records:
            record_keywords = self._record_keywords[id(record)]
            overlap = query_tokens & record_keywords
            if overlap:
                # Basic scoring: number of overlapping tokens
                score = len(overlap)

                # Boost if tag matches exactly
                for tag in record.tags:
                    tag_tokens = set(_tokenize(tag))
                    if query_tokens & tag_tokens:
                        score += len(query_tokens & tag_tokens) * 3

                # Boost if path contains exact keywords
                for q_token in query_tokens:
                    if q_token in record.path.lower():
                        score += 2

                scored_records.append((score, record))

        scored_records.sort(key=lambda x: x[0], reverse=True)
        return [record for score, record in scored_records[:top_k]]

    def get_exact_formatted(self, method: str, path: str) -> dict[str, Any] | None:
        """Get an exact endpoint record formatted nicely for the model."""
        method_upper = method.upper()
        for r in self.records:
            if r.method == method_upper and r.path == path:
                # Format parameters nicely
                params_list = []
                for p in r.schema.get("parameters", []):
                    required = " (REQUIRED)" if p.get("required") else ""
                    ptype = p.get("type", "")
                    desc = p.get("description", "")
                    params_list.append(f"{p['name']} ({p.get('in')}): {ptype}{required} - {desc}")

                # Format body schema
                body_schema = r.schema.get("request_body", {})
                body_formatted = _format_schema(body_schema) if body_schema else "No request body."

                result: dict[str, Any] = {
                    "method": r.method,
                    "path_template": r.path,
                    "summary": r.summary,
                    "description": r.description,
                    "parameters": params_list,
                    "request_body": body_formatted,
                    "csv_metadata": r.schema.get("_csv_metadata", {}),
                    "learned_rules": r.learned_rules,
                }

                # Surface BETA status as a top-level warning so the model cannot miss it
                csv_meta = r.schema.get("_csv_metadata", {})
                if csv_meta.get("notes", "").upper() == "BETA":
                    result["WARNING"] = (
                        "⚠️ BETA ENDPOINT: This endpoint is in BETA and restricted to pilot customers. "
                        "Calling it will return 403 Forbidden. DO NOT use this endpoint."
                    )

                return result
        return None


_CATALOG_INSTANCE: EndpointCatalog | None = None


def get_endpoint_catalog() -> EndpointCatalog:
    global _CATALOG_INSTANCE
    if _CATALOG_INSTANCE is None:
        _CATALOG_INSTANCE = EndpointCatalog()
    return _CATALOG_INSTANCE
