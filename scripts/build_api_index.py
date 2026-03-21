#!/usr/bin/env python3
"""Fetch the Tripletex OpenAPI spec and build a pre-indexed JSON grouped by resource tag.

Usage:
    python scripts/build_api_index.py

Output:
    src/ai_accounting_agent/api_index_data.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

SPEC_URL = "https://kkpqfuj-amager.tripletex.dev/v2/openapi.json"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "src" / "ai_accounting_agent" / "api_index_data.json"


def fetch_spec() -> dict[str, Any]:
    print(f"Fetching OpenAPI spec from {SPEC_URL} ...")
    resp = requests.get(SPEC_URL, timeout=60)
    resp.raise_for_status()
    spec = resp.json()
    print(f"  {len(spec.get('paths', {}))} paths, {len(spec.get('components', {}).get('schemas', {}))} schemas")
    return spec


def resolve_ref(ref: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a single $ref pointer (1 level only, no recursion)."""
    if not ref.startswith("#/"):
        return None
    parts = ref.lstrip("#/").split("/")
    node: Any = spec
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    if isinstance(node, dict):
        return dict(node)
    return None


def simplify_schema(schema: dict[str, Any] | None, spec: dict[str, Any], depth: int = 0) -> dict[str, Any] | None:
    """Simplify a JSON schema, resolving $ref 1 level deep."""
    if schema is None:
        return None

    if "$ref" in schema:
        if depth >= 1:
            ref_name = schema["$ref"].rsplit("/", 1)[-1]
            return {"$ref": ref_name, "type": "object"}
        resolved = resolve_ref(schema["$ref"], spec)
        if resolved:
            return simplify_schema(resolved, spec, depth + 1)
        return {"$ref": schema["$ref"]}

    result: dict[str, Any] = {}

    if "type" in schema:
        result["type"] = schema["type"]
    if "format" in schema:
        result["format"] = schema["format"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "description" in schema:
        desc = schema["description"]
        if len(desc) > 200:
            desc = desc[:200] + "..."
        result["description"] = desc
    if "required" in schema:
        result["required"] = schema["required"]
    if "default" in schema:
        result["default"] = schema["default"]

    if "properties" in schema:
        props: dict[str, Any] = {}
        for prop_name, prop_schema in schema["properties"].items():
            if prop_name in ("url", "changes"):
                continue
            props[prop_name] = simplify_schema(prop_schema, spec, depth + 1) or {"type": "unknown"}
        result["properties"] = props

    if "items" in schema:
        result["items"] = simplify_schema(schema["items"], spec, depth + 1)

    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for sub in schema["allOf"]:
            resolved = simplify_schema(sub, spec, depth)
            if resolved:
                merged.update(resolved)
        return merged or result

    return result or {"type": "unknown"}


def extract_parameters(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract simplified parameter info."""
    result = []
    for param in params:
        entry: dict[str, Any] = {
            "name": param.get("name", ""),
            "in": param.get("in", "query"),
        }
        if param.get("required"):
            entry["required"] = True
        if param.get("description"):
            desc = param["description"]
            if len(desc) > 150:
                desc = desc[:150] + "..."
            entry["description"] = desc
        schema = param.get("schema", {})
        if "type" in schema:
            entry["type"] = schema["type"]
        if "format" in schema:
            entry["format"] = schema["format"]
        if "enum" in schema:
            entry["enum"] = schema["enum"]
        if "default" in schema:
            entry["default"] = schema["default"]
        result.append(entry)
    return result


def build_index(spec: dict[str, Any]) -> dict[str, Any]:
    """Group all operations by their primary tag."""
    paths = spec.get("paths", {})
    index: dict[str, list[dict[str, Any]]] = {}

    for path, path_item in paths.items():
        for method in ("get", "post", "put", "delete", "patch"):
            if method not in path_item:
                continue
            operation = path_item[method]
            tags = operation.get("tags", ["_untagged"])
            primary_tag = tags[0] if tags else "_untagged"

            entry: dict[str, Any] = {
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary", ""),
            }

            params = extract_parameters(operation.get("parameters", []))
            if params:
                entry["parameters"] = params

            request_body = operation.get("requestBody", {})
            if request_body:
                content = request_body.get("content", {})
                for content_type, content_schema in content.items():
                    if "json" in content_type:
                        schema = simplify_schema(content_schema.get("schema"), spec)
                        if schema:
                            entry["request_body"] = schema
                        break

            index.setdefault(primary_tag, []).append(entry)

    return index


def main() -> None:
    spec = fetch_spec()
    index = build_index(spec)

    print(f"Built index with {len(index)} tag groups:")
    for tag, ops in sorted(index.items()):
        print(f"  {tag}: {len(ops)} operations")

    OUTPUT_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"\nWritten to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
