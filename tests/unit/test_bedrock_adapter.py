from toll_harness.core.types import ModelMessage, ToolDefinition
from toll_harness.models.bedrock import BedrockModelAdapter


class FakeBedrockClient:
    def __init__(self):
        self.request = None

    def converse(self, **request):
        self.request = request
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"text": "Saving state."},
                        {
                            "toolUse": {
                                "toolUseId": "t1",
                                "name": "state__save",
                                "input": {"checkpoint": {"status": "ready"}},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 12, "outputTokens": 8, "totalTokens": 20},
        }


def test_bedrock_converse_is_normalized():
    client = FakeBedrockClient()
    adapter = BedrockModelAdapter("us.example.model-v1", client=client)
    tool = ToolDefinition(
        "state.save",
        "Save state",
        {"type": "object", "properties": {"checkpoint": {"type": "object"}}},
    )

    response = adapter.invoke(
        system="system",
        messages=[ModelMessage.text("user", "goal")],
        tools=[tool],
    )

    assert client.request["toolConfig"]["tools"][0]["toolSpec"]["name"] == "state__save"
    assert response.tool_calls[0].name == "state.save"
    assert response.usage.total_tokens == 20
