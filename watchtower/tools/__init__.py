from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..models import FinalizationProposal, Model, URLInput
from .documentation import inspect_documentation
from .extraction import APIInput, RecipeInput, test_api, test_extraction
from .finalization import finalize_exploration
from .inspection import (
    inspect_documents,
    inspect_network,
    inspect_page,
    inspect_scripts,
    inspect_tender_details,
)


@dataclass(frozen=True)
class Tool:
    description: str
    schema: type[Model]
    handler: Callable[..., Awaitable[dict]]


REGISTRY = {
    "inspect_documentation": Tool(
        "Read linked public API documentation and OpenAPI/Swagger specifications.",
        URLInput,
        inspect_documentation,
    ),
    "inspect_page": Tool(
        "Inspect an observed HTML page for listings, navigation and selectors.", URLInput, inspect_page
    ),
    "inspect_network": Tool(
        "Render an observed page and inspect actual public Fetch/XHR traffic.", URLInput, inspect_network
    ),
    "inspect_scripts": Tool(
        "Inspect embedded JSON and hydration state on an observed page.", URLInput, inspect_scripts
    ),
    "test_api": Tool(
        "Probe an observed public API URL. Supply records/fields or infer common tender shapes.",
        APIInput,
        test_api,
    ),
    "inspect_tender_details": Tool(
        "Read an observed tender detail and find structured fields and attachments.",
        URLInput,
        inspect_tender_details,
    ),
    "inspect_documents": Tool(
        "Range-probe one observed attachment using a bounded read.", URLInput, inspect_documents
    ),
    "test_extraction": Tool(
        "Validate a declarative recipe against live records and its declared steps.",
        RecipeInput,
        test_extraction,
    ),
    "finalize_exploration": Tool(
        "Request finalization with a verified recipe or explain insufficient evidence.",
        FinalizationProposal,
        finalize_exploration,
    ),
}


def _clean_schema(value):
    if isinstance(value, dict):
        return {
            key: _clean_schema(item)
            for key, item in value.items()
            if key
            not in {
                "title",
                "default",
                "description",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
                "minProperties",
                "maxProperties",
                "minimum",
                "maximum",
                "propertyNames",
            }
        }
    if isinstance(value, list):
        return [_clean_schema(item) for item in value]
    return value


# The model sees a compact schema. Each handler's full Pydantic model validates arguments at runtime.
OPENAI_TOOLS = [
    {
        "type": "function",
        "name": name,
        "description": tool.description,
        "strict": False,
        "parameters": _clean_schema(tool.schema.model_json_schema()),
    }
    for name, tool in REGISTRY.items()
]
