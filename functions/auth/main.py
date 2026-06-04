"""Auth function — validates Amazon credentials and confirms token exchange works.

The SDKs handle token exchange internally, so pipeline functions self-authenticate.
This function's job is to fail fast if credentials are missing or invalid before
the workflow spends time on report creation. It also returns the access_token for
any step that might need raw API access.
"""

from __future__ import annotations

import logging

import flask

from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.lwa_token import get_access_token

logger = logging.getLogger(__name__)


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    if not api_source or not client_id:
        return {"error": "Missing api_source or client_id", "code": "INVALID_REQUEST"}, 400

    try:
        if api_source == "sp_api":
            creds = get_sp_credentials(client_id)
            access_token = get_access_token(
                refresh_token=creds["refresh_token"],
                client_id=creds["lwa_app_id"],
                client_secret=creds["lwa_client_secret"],
            )
            return {"access_token": access_token, "api_source": "sp_api"}, 200

        elif api_source == "ads_api":
            creds = get_ads_credentials(client_id)
            access_token = get_access_token(
                refresh_token=creds["refresh_token"],
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
            return {
                "access_token": access_token,
                "api_source": "ads_api",
                "ads_client_id": creds["client_id"],
                "ads_profile_id": creds.get("profile_id"),
            }, 200

        else:
            return {"error": f"Unknown api_source: {api_source}", "code": "INVALID_SOURCE"}, 400

    except ValueError as exc:
        return {"error": str(exc), "code": "CREDENTIAL_ERROR"}, 400
    except Exception:
        logger.exception("Auth failed", extra={"client_id": client_id, "api_source": api_source})
        return {"error": "Token exchange failed", "code": "AUTH_FAILED"}, 500
