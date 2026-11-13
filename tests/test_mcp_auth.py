import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mcp_auth import BearerTokenMiddleware, run_authenticated_mcp


class RecordingApp:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


class McpBearerAuthenticationTests(unittest.TestCase):
    TOKEN = "test-only-" + "x" * 32

    def invoke(self, middleware, headers):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(
            middleware(
                {"type": "http", "method": "POST", "path": "/mcp", "headers": headers},
                receive,
                send,
            )
        )
        return messages

    def test_missing_or_invalid_token_is_rejected(self):
        app = RecordingApp()
        middleware = BearerTokenMiddleware(app, self.TOKEN)

        missing = self.invoke(middleware, [])
        invalid = self.invoke(middleware, [(b"authorization", b"Bearer wrong-token")])

        self.assertEqual(missing[0]["status"], 401)
        self.assertEqual(invalid[0]["status"], 401)
        self.assertEqual(app.calls, 0)

    def test_valid_bearer_token_reaches_mcp_app(self):
        app = RecordingApp()
        middleware = BearerTokenMiddleware(app, self.TOKEN)

        messages = self.invoke(
            middleware,
            [(b"authorization", f"bEaReR {self.TOKEN}".encode("ascii"))],
        )

        self.assertEqual(messages[0]["status"], 200)
        self.assertEqual(app.calls, 1)

    def test_duplicate_authorization_headers_are_rejected(self):
        app = RecordingApp()
        middleware = BearerTokenMiddleware(app, self.TOKEN)
        header = (b"authorization", f"Bearer {self.TOKEN}".encode("ascii"))

        messages = self.invoke(middleware, [header, header])

        self.assertEqual(messages[0]["status"], 401)
        self.assertEqual(app.calls, 0)

    def test_short_token_prevents_server_start(self):
        with patch("mcp_auth.env", return_value="too-short"):
            with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
                run_authenticated_mcp(object())

    def test_runner_wraps_mcp_app_and_keeps_loopback_default(self):
        app = RecordingApp()

        class FakeServer:
            settings = SimpleNamespace(log_level="WARNING")

            def streamable_http_app(self, host):
                self.host = host
                return app

        values = {"MCP_BEARER_TOKEN": self.TOKEN}
        env = lambda name, default=None: values.get(name, default)
        server = FakeServer()

        with patch("mcp_auth.env", side_effect=env):
            with patch("uvicorn.run") as run:
                run_authenticated_mcp(server)

        wrapped_app = run.call_args.args[0]
        self.assertIsInstance(wrapped_app, BearerTokenMiddleware)
        self.assertIs(wrapped_app.app, app)
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 8000)


if __name__ == "__main__":
    unittest.main()
