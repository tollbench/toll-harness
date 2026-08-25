from toll_harness.core.types import ToolDefinition

OPERATOR_CAPABILITIES = [
    ToolDefinition(
        "operator.observe",
        "Observe current run status, checkpoint, and immutable events.",
        {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        "operator.message",
        "Append a live operator message to a Supported run.",
        {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["run_id", "message"],
            "additionalProperties": False,
        },
    ),
]
