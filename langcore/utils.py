"""Utility functions for langcore cog"""

from typing import Any, Dict
import logging

log = logging.getLogger("red.langcore.utils")


def validate_function_schema(schema: Dict[str, Any]) -> str:
    """Return an error message when a function schema is invalid, else empty string."""
    missing = ""
    if "name" not in schema:
        missing += "- `name`\n"
    if "description" not in schema:
        missing += "- `description`\n"
    if "parameters" not in schema:
        missing += "- `parameters`\n"
    if "parameters" in schema:
        params = schema["parameters"]
        if "type" not in params:
            missing += "- `type` in **parameters**\n"
        if "properties" not in params:
            missing += "- `properties` in **parameters**\n"
        if "required" in params.get("properties", []):
            missing += "- `required` key needs to be outside of properties!\n"
    return missing
