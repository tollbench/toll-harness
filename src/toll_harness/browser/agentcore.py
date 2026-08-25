from __future__ import annotations

import base64
import datetime
import secrets
import uuid
from urllib.parse import urlparse

from toll_harness.browser.playwright import PlaywrightBrowserProvider


class AgentCoreBrowserProvider(PlaywrightBrowserProvider):
    """Amazon AgentCore Browser transport for the provider-neutral Toll schema."""

    def __init__(
        self,
        *,
        region: str = "us-west-2",
        profile_name: str | None = None,
        browser_identifier: str = "aws.browser.v1",
        session_timeout_seconds: int = 3600,
        max_text_chars: int = 20_000,
    ):
        try:
            import boto3
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("Install Toll Harness with the 'agentcore' extra") from error

        self._session = boto3.Session(profile_name=profile_name, region_name=region)
        self._agentcore = self._session.client("bedrock-agentcore")
        response = self._agentcore.start_browser_session(
            browserIdentifier=browser_identifier,
            name=f"toll-harness-{uuid.uuid4().hex[:8]}",
            sessionTimeoutSeconds=session_timeout_seconds,
        )
        self._browser_identifier = response["browserIdentifier"]
        self._session_id = response["sessionId"]

        endpoint = urlparse(self._agentcore.meta.endpoint_url)
        host = endpoint.netloc
        path = f"/browser-streams/{self._browser_identifier}/sessions/{self._session_id}/automation"
        websocket_url = f"wss://{host}{path}"
        credentials = self._session.get_credentials()
        if credentials is None:
            self.close_session()
            raise RuntimeError("No AWS credentials are available for AgentCore Browser")
        frozen = credentials.get_frozen_credentials()
        request = AWSRequest(
            method="GET",
            url=f"https://{host}{path}",
            headers={
                "host": host,
                "x-amz-date": datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y%m%dT%H%M%SZ"
                ),
            },
        )
        SigV4Auth(frozen, "bedrock-agentcore", region).add_auth(request)
        headers = {
            "Host": host,
            "X-Amz-Date": request.headers["x-amz-date"],
            "Authorization": request.headers["Authorization"],
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
        }
        if frozen.token:
            headers["X-Amz-Security-Token"] = frozen.token

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(
                websocket_url, headers=headers
            )
            context = (
                self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            )
            self._page = context.pages[0] if context.pages else context.new_page()
            self._protect_page()
        except Exception:
            self._playwright.stop()
            self.close_session()
            raise
        self._refs = {}
        self.max_text_chars = max_text_chars

    def close_session(self) -> None:
        if getattr(self, "_session_id", None):
            self._agentcore.stop_browser_session(
                browserIdentifier=self._browser_identifier,
                sessionId=self._session_id,
            )
            self._session_id = None

    def close(self) -> None:
        try:
            self._browser.close()
            self._playwright.stop()
        finally:
            self.close_session()
