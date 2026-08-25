"""Dependency-light protocol adapters for Veridian action assurance.

Adapters only validate and normalize proposed actions; they never dispatch a
tool. Mapping and attribute-object inputs are bound as canonical profile bytes.
Byte inputs must already use that profile and are bound verbatim. Business
semantics and transport provenance remain separate returned objects.
"""

from ._errors import AdapterError, AdapterValidationError, UnknownActionError
from ._generic import GenericActionAdapter
from ._langgraph import LangGraphToolCallAdapter
from ._mcp import MCPToolCallAdapter
from ._model import ActionAdapter, ActionSpecV1, NormalizedActionV1
from ._openai import OpenAIResponsesAdapter
from ._pydantic_ai import (
    PYDANTIC_AI_DEFERRED_TOOL_PROFILE_V1,
    PydanticAIDeferredToolAdapter,
)

__all__ = [
    "ActionSpecV1",
    "ActionAdapter",
    "AdapterError",
    "AdapterValidationError",
    "GenericActionAdapter",
    "LangGraphToolCallAdapter",
    "MCPToolCallAdapter",
    "NormalizedActionV1",
    "OpenAIResponsesAdapter",
    "PYDANTIC_AI_DEFERRED_TOOL_PROFILE_V1",
    "PydanticAIDeferredToolAdapter",
    "UnknownActionError",
]
