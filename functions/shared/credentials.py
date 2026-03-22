"""Build Amazon SDK credentials from Firestore client config + Secret Manager.

Each pipeline function calls get_sp_credentials() or get_ads_credentials()
with a client_id. This reads the client's config from Firestore, fetches
app-level and client-level secrets from Secret Manager, and returns a
credentials dict ready for the SDK constructors.
"""

from __future__ import annotations

import json
import logging

from google.cloud import secretmanager

from shared.config import get_environment, get_project
from shared.firestore_utils import get_client

logger = logging.getLogger(__name__)

_sm: secretmanager.SecretManagerServiceClient | None = None


def _get_sm() -> secretmanager.SecretManagerServiceClient:
    global _sm
    if _sm is None:
        _sm = secretmanager.SecretManagerServiceClient()
    return _sm


def _read_secret(secret_name: str) -> dict:
    project = get_project()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=name)
    return json.loads(resp.payload.data.decode("utf-8"))


def get_sp_credentials(client_id: str) -> dict:
    """Build credentials dict for python-amazon-sp-api SDK.

    Returns: {"refresh_token": ..., "lwa_app_id": ..., "lwa_client_secret": ...}
    """
    client = get_client(client_id)
    if not client:
        raise ValueError(f"Client '{client_id}' not found")

    secret_name = client.get("sp_api_secret_name")
    if not secret_name:
        raise ValueError(f"Client '{client_id}' has no SP API credentials configured")

    env = get_environment()
    app_creds = _read_secret(f"kalilos-{env}-sp-api-app-credentials")
    client_creds = _read_secret(secret_name)

    return {
        "refresh_token": client_creds["refresh_token"],
        "lwa_app_id": app_creds["client_id"],
        "lwa_client_secret": app_creds["client_secret"],
    }


def get_ads_credentials(client_id: str) -> dict:
    """Build credentials dict for python-amazon-ad-api SDK.

    Two paths:
      1. New: client has ads_profile_id in Firestore, refresh_token in app secret
      2. Legacy: client has ads_api_secret_name pointing to a per-client secret

    Returns: {"refresh_token": ..., "client_id": ..., "client_secret": ..., "profile_id": ...}
    """
    client = get_client(client_id)
    if not client:
        raise ValueError(f"Client '{client_id}' not found")

    env = get_environment()
    app_creds = _read_secret(f"kalilos-{env}-ads-api-app-credentials")

    profile_id = client.get("ads_profile_id", "")
    secret_name = client.get("ads_api_secret_name")

    if secret_name:
        client_creds = _read_secret(secret_name)
        return {
            "refresh_token": client_creds["refresh_token"],
            "client_id": app_creds["client_id"],
            "client_secret": app_creds["client_secret"],
            "profile_id": client_creds.get("profile_id", profile_id),
        }

    if profile_id:
        refresh_token = app_creds.get("refresh_token")
        if not refresh_token:
            sp_secret = client.get("sp_api_secret_name")
            if sp_secret:
                sp_creds = _read_secret(sp_secret)
                refresh_token = sp_creds.get("refresh_token")
        if not refresh_token:
            raise ValueError(
                f"Client '{client_id}' has ads_profile_id but no refresh_token: "
                "add refresh_token to the ads-api-app-credentials secret, "
                "set ads_api_secret_name on the client, or ensure sp_api_secret_name is set"
            )
        return {
            "refresh_token": refresh_token,
            "client_id": app_creds["client_id"],
            "client_secret": app_creds["client_secret"],
            "profile_id": profile_id,
        }

    raise ValueError(f"Client '{client_id}' has no Ads API credentials configured")
