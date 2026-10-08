"""OpenAPI/Swagger JSON parser for T2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._common import get_logger, make_document, read_utf8


def _json_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _format_operation(method: str, path: str, operation: dict[str, Any], path_item: dict[str, Any]) -> list[str]:
    lines = [f"### {method.upper()} {path}"]
    for field in ("operationId", "summary", "description", "tags"):
        if operation.get(field):
            lines.append(f"{field}: {_json_value(operation[field])}")

    parameters = [*path_item.get("parameters", []), *operation.get("parameters", [])]
    if parameters:
        lines.append("Parameters:")
        for parameter in parameters:
            name = parameter.get("name", "unknown")
            location = parameter.get("in", "unknown")
            required = "required" if parameter.get("required") else "optional"
            description = parameter.get("description", "")
            schema = parameter.get("schema", parameter.get("type", ""))
            lines.append(
                f"- {name} ({location}, {required}): {description}; schema={_json_value(schema)}"
            )

    request_body = operation.get("requestBody")
    if request_body:
        lines.append(f"Request body: {_json_value(request_body)}")
    if operation.get("responses"):
        lines.append("Responses:")
        for status, response in operation["responses"].items():
            description = response.get("description", "") if isinstance(response, dict) else response
            lines.append(f"- {status}: {description}")
    return lines


class OpenAPILoader:
    """Convert an OpenAPI 3 or Swagger 2 JSON document into readable API text."""

    def load(self, file_path: str) -> list:
        path = Path(file_path)
        log = get_logger()
        try:
            payload = json.loads(read_utf8(path))
            if not isinstance(payload, dict) or not (payload.get("openapi") or payload.get("swagger")):
                raise ValueError("JSON is not an OpenAPI 3 or Swagger 2 document")

            info = payload.get("info", {})
            lines = [f"# {info.get('title', path.stem)}"]
            if info.get("version"):
                lines.append(f"version: {info['version']}")
            if info.get("description"):
                lines.append(f"description: {info['description']}")
            for server in payload.get("servers", []):
                if server.get("url"):
                    lines.append(f"server: {server['url']}")
            for path_name, path_item in payload.get("paths", {}).items():
                if not isinstance(path_item, dict):
                    continue
                for method, operation in path_item.items():
                    if method.lower() in {"parameters", "summary", "description", "servers"}:
                        continue
                    if isinstance(operation, dict):
                        lines.extend(_format_operation(method, path_name, operation, path_item))
            document = make_document("\n".join(lines), path, 1, "api")
        except Exception:
            log.exception("Failed to parse OpenAPI JSON: source=%s", path)
            raise
        documents = [document] if document is not None else []
        log.info("Parsed OpenAPI JSON: source=%s documents=%s", path.name, len(documents))
        return documents
