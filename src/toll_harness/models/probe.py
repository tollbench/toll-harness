from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ProbeResult:
    intelligence: str
    provider: str
    identifier: str | None
    invocation_successful: bool
    tool_use_supported: bool | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROVIDER_LABELS = {
    "anthropic": "Claude",
    "openai": "GPT",
    "xai": "Grok",
    "mistral ai": "Mistral",
    "mistral": "Mistral",
    "deepseek": "DeepSeek",
    "moonshot ai": "Kimi",
    "moonshot": "Kimi",
    "qwen": "Qwen",
    "meta": "Llama",
    "amazon": "Nova",
    "z.ai": "GLM",
    "zai": "GLM",
}


def _error(error: Exception) -> str:
    details = getattr(error, "response", {}).get("Error", {})
    code = details.get("Code", type(error).__name__)
    message = details.get("Message", str(error))
    return f"{code}: {message}"


class BedrockProbe:
    def __init__(
        self,
        *,
        region: str = "us-west-2",
        profile_name: str | None = None,
        session: Any | None = None,
    ):
        if session is None:
            import boto3

            session = boto3.Session(profile_name=profile_name, region_name=region)
        self.region = region
        self.session = session
        self.catalog = session.client("bedrock")
        self.runtime = session.client("bedrock-runtime")

    def identity(self) -> dict[str, Any]:
        response = self.session.client("sts").get_caller_identity()
        credentials = self.session.get_credentials()
        return {
            "arn": response.get("Arn"),
            "account": response.get("Account"),
            "credential_method": getattr(credentials, "method", None),
            "region": self.region,
        }

    def discover(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        models = self.catalog.list_foundation_models().get("modelSummaries", [])
        profiles: list[dict[str, Any]] = []
        token = None
        while True:
            arguments = {"maxResults": 1000}
            if token:
                arguments["nextToken"] = token
            response = self.catalog.list_inference_profiles(**arguments)
            profiles.extend(response.get("inferenceProfileSummaries", []))
            token = response.get("nextToken")
            if not token:
                break
        return models, profiles

    @staticmethod
    def _provider_label(provider: str, model_id: str) -> str:
        normalized = provider.lower().strip()
        if normalized in PROVIDER_LABELS:
            return PROVIDER_LABELS[normalized]
        for key, value in PROVIDER_LABELS.items():
            if key in normalized or model_id.lower().startswith(key.replace(" ", "")):
                return value
        return provider

    @staticmethod
    def _score(model: dict[str, Any]) -> tuple[int, str]:
        value = f"{model.get('modelId', '')} {model.get('modelName', '')}".lower()
        score = 0
        for preferred in ("sonnet", "large", "pro", "max", "instruct", "nova-2"):
            score += 10 if preferred in value else 0
        for unsuitable in ("embed", "image", "video", "audio", "rerank", "guard"):
            score -= 100 if unsuitable in value else 0
        if "TEXT" in model.get("outputModalities", []):
            score += 20
        if model.get("modelLifecycle", {}).get("status") == "ACTIVE":
            score += 10
        return score, value

    @staticmethod
    def _identifiers(model: dict[str, Any], profiles: list[dict[str, Any]]) -> list[str]:
        model_id = model["modelId"]
        identifiers: list[str] = []
        if "ON_DEMAND" in model.get("inferenceTypesSupported", []):
            identifiers.append(model_id)
        matching_profiles = []
        for profile in profiles:
            for target in profile.get("models", []):
                if target.get("modelArn", "").endswith(f"foundation-model/{model_id}"):
                    matching_profiles.append(profile["inferenceProfileId"])
                    break
        identifiers.extend(
            sorted(matching_profiles, key=lambda item: (not item.startswith("us."), item))
        )
        if not identifiers:
            identifiers.append(model_id)
        return list(dict.fromkeys(identifiers))

    def _invoke_text(self, identifier: str) -> None:
        self.runtime.converse(
            modelId=identifier,
            messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )

    def _probe_tool(self, identifier: str) -> bool:
        response = self.runtime.converse(
            modelId=identifier,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Call probe_tool with value set to ok. Do not answer in text."}
                    ],
                }
            ],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "probe_tool",
                            "description": "Required probe tool.",
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {"value": {"type": "string"}},
                                    "required": ["value"],
                                }
                            },
                        }
                    }
                ]
            },
            inferenceConfig={"maxTokens": 64, "temperature": 0},
        )
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        return any("toolUse" in block for block in blocks)

    def run(
        self,
        *,
        max_providers: int = 10,
        attempts_per_provider: int = 5,
        preferred_intelligences: list[str] | None = None,
    ) -> list[ProbeResult]:
        try:
            models, profiles = self.discover()
        except Exception as error:
            return [
                ProbeResult(
                    intelligence="Discovery blocked",
                    provider="Amazon Bedrock",
                    identifier=None,
                    invocation_successful=False,
                    tool_use_supported=None,
                    error=_error(error),
                )
            ]

        grouped: dict[str, list[dict[str, Any]]] = {}
        for model in models:
            provider = model.get("providerName", "Unknown")
            label = self._provider_label(provider, model.get("modelId", ""))
            grouped.setdefault(label, []).append(model)

        default_order = [
            "Claude",
            "GPT",
            "Grok",
            "Mistral",
            "DeepSeek",
            "Kimi",
            "Qwen",
            "Llama",
            "Nova",
            "GLM",
        ]
        desired_order = list(dict.fromkeys([*(preferred_intelligences or []), *default_order]))
        providers = sorted(
            grouped,
            key=lambda item: (
                item not in desired_order,
                desired_order.index(item) if item in desired_order else item,
            ),
        )
        results: list[ProbeResult] = []
        for intelligence in providers[:max_providers]:
            candidates = sorted(grouped[intelligence], key=self._score, reverse=True)
            last_error = "No candidate was invokable"
            selected_provider = (
                candidates[0].get("providerName", "Unknown") if candidates else "Unknown"
            )
            selected_identifier = None
            attempts = 0
            success = False
            for model in candidates:
                for identifier in self._identifiers(model, profiles):
                    selected_identifier = identifier
                    attempts += 1
                    try:
                        self._invoke_text(identifier)
                        success = True
                        break
                    except Exception as error:
                        last_error = _error(error)
                    if attempts >= attempts_per_provider:
                        break
                if success or attempts >= attempts_per_provider:
                    break
            if not success:
                results.append(
                    ProbeResult(
                        intelligence,
                        selected_provider,
                        selected_identifier,
                        False,
                        None,
                        last_error,
                    )
                )
                continue
            try:
                tool_support = self._probe_tool(selected_identifier)
                tool_error = (
                    None if tool_support else "Invocation succeeded but no tool call was produced"
                )
            except Exception as error:
                tool_support = False
                tool_error = _error(error)
            results.append(
                ProbeResult(
                    intelligence,
                    selected_provider,
                    selected_identifier,
                    True,
                    tool_support,
                    tool_error,
                )
            )
        return results
