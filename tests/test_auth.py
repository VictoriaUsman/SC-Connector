"""Tests for the auth Cloud Function error surfacing.

Regression coverage for reviewer feedback on CU-868jxen7m: a failed LWA token
exchange must surface a clean, actionable message — never the bare
``Token exchange failed`` string nor a raw HTTP error dump.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))


def _load_handler(function_name: str) -> ModuleType:
    path = (
        Path(__file__).resolve().parent.parent
        / "functions"
        / function_name
        / "main.py"
    )
    module_name = f"_handler_{function_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestAuthTokenExchangeFailure:
    def test_returns_actionable_auth_failed_message(self):
        auth_main = _load_handler("auth")

        request = MagicMock()
        request.get_json.return_value = {
            "api_source": "sp_api",
            "client_id": "pico",
        }

        with (
            patch.object(
                auth_main,
                "get_sp_credentials",
                return_value={
                    "refresh_token": "x",
                    "lwa_app_id": "a",
                    "lwa_client_secret": "s",
                },
            ),
            patch.object(
                auth_main,
                "get_access_token",
                side_effect=RuntimeError("boom from LWA endpoint"),
            ),
        ):
            body, status = auth_main.handler(request)

        assert status == 500
        assert body["code"] == "AUTH_FAILED"
        # The surfaced message must be human-readable and actionable, not the
        # old bare string and not a raw exception/HTTP dump.
        assert body["error"] != "Token exchange failed"
        assert "Re-authorize" in body["error"]
        assert "Seller Central" in body["error"]
        # Never leak the raw exception text into the user-facing message.
        assert "boom from LWA endpoint" not in body["error"]
