# Local Dev Sandbox Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `functions/daily_recap` fully locally — reading bot configs from a JSON-backed local Firestore substitute, querying Supabase (standing in for BigQuery) for a day's metrics, and posting a real message to a real Slack channel — with zero calls to GCP.

**Architecture:** A single `LOCAL_MODE` env var gates three independent, additive bypasses: Secret Manager → a local JSON secrets file; `firestore.Client()` → a JSON-file-backed fake sharing the frontend mock API's data file; BigQuery → a new Supabase (Postgres) query module selected by a separate `METRICS_BACKEND` env var. Every bypass defaults to today's real behavior, so production is untouched when the env vars are unset.

**Tech Stack:** Python 3.12, `psycopg2` (Postgres driver, lazily imported), `functions-framework` (existing local-fn runner), pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-local-dev-sandbox-phase1-design.md`

## Global Constraints

- `LOCAL_MODE` unset (or not `"true"`, case-insensitive) must produce byte-for-byte identical behavior to today in every touched file — no exceptions.
- `METRICS_BACKEND` defaults to `"bigquery"` when unset.
- Secret names used in `scripts/local-secrets.json` are the exact same short secret-id strings Secret Manager uses today (e.g. `kalilos-staging-slack-bot-token`) — no renaming.
- `psycopg2` is imported lazily (inside a function, not at module top-level) everywhere it's used, so a production deploy on the BigQuery path never requires it at import time.
- This environment does not have `make` installed (Windows, Git Bash) — every verification step in this plan uses the underlying script directly (e.g. `bash scripts/run-local.sh ...`) as the primary command, noting the `make` equivalent for environments that have it.
- In this environment, a bare `python`/`python3` on PATH is a non-functional Windows Store shim; use `py` (confirmed working) or the full interpreter path instead, everywhere a step says `Run: python ...` or `pip install ...`. Other environments with a normal `python3` on PATH can use these commands as written.
- Test files added by this plan follow the existing convention: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))` at the top, then import from `shared.<module>`.

---

### Task 1: Local secrets module

**Files:**
- Create: `functions/shared/local_secrets.py`
- Test: `tests/test_local_secrets.py`

**Interfaces:**
- Produces: `is_local_mode() -> bool`, `resolve_secret(secret_name: str) -> str` — both imported by Tasks 2 and 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_local_secrets.py`:

```python
"""Tests for shared.local_secrets — LOCAL_MODE secret resolution."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import pytest

from shared import local_secrets


@pytest.fixture(autouse=True)
def _reset_cache():
    local_secrets._local_secrets_cache = None
    yield
    local_secrets._local_secrets_cache = None


class TestIsLocalMode:
    def test_true_when_env_var_true(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "true")
        assert local_secrets.is_local_mode() is True

    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        assert local_secrets.is_local_mode() is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "TRUE")
        assert local_secrets.is_local_mode() is True

    def test_false_for_other_values(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "false")
        assert local_secrets.is_local_mode() is False


class TestResolveSecret:
    def test_reads_value_from_file(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({"my-secret": "xoxb-abc123"}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        assert local_secrets.resolve_secret("my-secret") == "xoxb-abc123"

    def test_missing_key_raises_with_helpful_message(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        with pytest.raises(KeyError, match="my-secret"):
            local_secrets.resolve_secret("my-secret")

    def test_missing_file_raises_with_helpful_message(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "does-not-exist.json"
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        with pytest.raises(FileNotFoundError, match="local-secret-set"):
            local_secrets.resolve_secret("my-secret")

    def test_caches_after_first_load(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({"a": "1"}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        local_secrets.resolve_secret("a")
        secrets_file.write_text(json.dumps({"a": "2"}))
        assert local_secrets.resolve_secret("a") == "1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_local_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.local_secrets'`

- [ ] **Step 3: Write the implementation**

Create `functions/shared/local_secrets.py`:

```python
"""Local-mode secret resolution — bypasses Secret Manager when LOCAL_MODE=true.

scripts/local-secrets.json (gitignored) holds {secret_name: raw_payload}
using the exact same secret names Secret Manager uses in staging/prod, so
switching between local and real secrets is just flipping LOCAL_MODE.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_LOCAL_SECRETS_FILE = Path(__file__).resolve().parents[2] / "scripts" / "local-secrets.json"

_local_secrets_cache: dict[str, str] | None = None


def is_local_mode() -> bool:
    return os.environ.get("LOCAL_MODE", "").lower() == "true"


def _load_local_secrets() -> dict[str, str]:
    global _local_secrets_cache
    if _local_secrets_cache is None:
        if not _LOCAL_SECRETS_FILE.exists():
            raise FileNotFoundError(
                f"LOCAL_MODE is set but {_LOCAL_SECRETS_FILE} does not exist. "
                f"Create it with `make local-secret-set NAME=<secret_name> VALUE=<value>`."
            )
        _local_secrets_cache = json.loads(_LOCAL_SECRETS_FILE.read_text())
    return _local_secrets_cache


def resolve_secret(secret_name: str) -> str:
    """Return a secret's raw payload string from scripts/local-secrets.json."""
    secrets = _load_local_secrets()
    if secret_name not in secrets:
        raise KeyError(
            f"'{secret_name}' not found in {_LOCAL_SECRETS_FILE}. "
            f"Add it with `make local-secret-set NAME={secret_name} VALUE=<value>`."
        )
    return secrets[secret_name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_local_secrets.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/local_secrets.py tests/test_local_secrets.py
git commit -m "Add local secrets module for LOCAL_MODE"
```

---

### Task 2: Wire local secrets into the Slack token loader

**Files:**
- Modify: `functions/shared/slack_client.py:149-161` (`_get_slack_token`)
- Test: `tests/test_slack_client.py`

**Interfaces:**
- Consumes: `is_local_mode()`, `resolve_secret(secret_name: str) -> str` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_slack_client.py` (add `from unittest.mock import MagicMock, patch` to the existing imports at the top of the file):

```python
class TestGetSlackToken:
    def test_local_mode_uses_local_secrets(self, monkeypatch):
        import shared.slack_client as sc

        sc._slack_token = None
        monkeypatch.setenv("LOCAL_MODE", "true")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        try:
            with patch("shared.slack_client.resolve_secret", return_value="xoxb-local-token") as mock_resolve:
                token = sc._get_slack_token()
            assert token == "xoxb-local-token"
            mock_resolve.assert_called_once_with("kalilos-staging-slack-bot-token")
        finally:
            sc._slack_token = None

    def test_production_mode_uses_secret_manager(self, monkeypatch):
        import shared.slack_client as sc

        sc._slack_token = None
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("GCP_PROJECT", "test-project")
        fake_resp = MagicMock()
        fake_resp.payload.data = b"xoxb-real-token"
        try:
            with patch("shared.slack_client._get_sm") as mock_sm:
                mock_sm.return_value.access_secret_version.return_value = fake_resp
                token = sc._get_slack_token()
            assert token == "xoxb-real-token"
        finally:
            sc._slack_token = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_slack_client.py -v -k TestGetSlackToken`
Expected: FAIL — `ImportError: cannot import name 'resolve_secret' from 'shared.slack_client'` (patch target doesn't exist yet)

- [ ] **Step 3: Write the implementation**

In `functions/shared/slack_client.py`, add the import near the top (after the existing `from shared.config import get_environment, get_project` line):

```python
from shared.local_secrets import is_local_mode, resolve_secret
```

Replace the `_get_slack_token` function body:

```python
def _get_slack_token() -> str:
    """Lazily load the Slack bot token from Secret Manager (or
    scripts/local-secrets.json when LOCAL_MODE=true)."""
    global _slack_token
    if _slack_token is not None:
        return _slack_token

    env = get_environment()
    short_name = f"kalilos-{env}-slack-bot-token"

    if is_local_mode():
        _slack_token = resolve_secret(short_name).strip()
        return _slack_token

    project = get_project()
    full_name = f"projects/{project}/secrets/{short_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=full_name)
    _slack_token = resp.payload.data.decode("utf-8").strip()
    return _slack_token
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_slack_client.py -v`
Expected: PASS (all tests in the file, including the pre-existing `resolve_target_channels` tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/slack_client.py tests/test_slack_client.py
git commit -m "Wire local secrets bypass into Slack token loader"
```

---

### Task 3: Wire local secrets into Amazon credential loading

**Files:**
- Modify: `functions/shared/credentials.py:31-35` (`_read_secret`)
- Test: `tests/test_credentials.py`

**Interfaces:**
- Consumes: `is_local_mode()`, `resolve_secret(secret_name: str) -> str` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_credentials.py` (add `MagicMock` to the existing `from unittest.mock import patch` import line, making it `from unittest.mock import MagicMock, patch`):

```python
class TestReadSecret:
    def test_local_mode_reads_from_local_secrets(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "true")
        try:
            with patch("shared.credentials.resolve_secret", return_value='{"client_id": "abc"}') as mock_resolve:
                result = credentials._read_secret("kalilos-staging-sp-api-app-credentials")
            assert result == {"client_id": "abc"}
            mock_resolve.assert_called_once_with("kalilos-staging-sp-api-app-credentials")
        finally:
            monkeypatch.delenv("LOCAL_MODE", raising=False)

    def test_production_mode_uses_secret_manager(self, monkeypatch):
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        fake_resp = MagicMock()
        fake_resp.payload.data = b'{"client_id": "real"}'
        with patch.object(credentials, "_get_sm") as mock_sm:
            mock_sm.return_value.access_secret_version.return_value = fake_resp
            result = credentials._read_secret("kalilos-staging-sp-api-app-credentials")
        assert result == {"client_id": "real"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_credentials.py -v -k TestReadSecret`
Expected: FAIL — `ImportError: cannot import name 'resolve_secret' from 'shared.credentials'`

- [ ] **Step 3: Write the implementation**

In `functions/shared/credentials.py`, add the import (after `from shared.firestore_utils import get_client`):

```python
from shared.local_secrets import is_local_mode, resolve_secret
```

Replace `_read_secret`:

```python
def _read_secret(secret_name: str) -> dict:
    if is_local_mode():
        return json.loads(resolve_secret(secret_name))

    project = get_project()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=name)
    return json.loads(resp.payload.data.decode("utf-8"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_credentials.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/credentials.py tests/test_credentials.py
git commit -m "Wire local secrets bypass into Amazon credential loading"
```

---

### Task 4: Local Firestore shim

**Files:**
- Create: `functions/shared/local_firestore.py`
- Test: `tests/test_local_firestore.py`

**Interfaces:**
- Produces: `LocalFirestoreClient(data_file: Path)` with `.collection(name: str) -> LocalCollectionRef`; `LocalCollectionRef.document(doc_id: str | None = None) -> LocalDocRef` and `.stream() -> list[LocalDocSnapshot]`; `LocalDocRef.get() -> LocalDocSnapshot`, `.set(data: dict, merge: bool = False) -> None`, `.id`; `LocalDocSnapshot.id`, `.exists`, `.to_dict() -> dict | None`. Consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_local_firestore.py`:

```python
"""Tests for shared.local_firestore — JSON-backed fake Firestore client."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.local_firestore import LocalFirestoreClient


class TestGetSet:
    def test_get_missing_doc_does_not_exist(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        snap = client.collection("clients").document("nope").get()
        assert snap.exists is False
        assert snap.to_dict() is None

    def test_set_then_get_roundtrips(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        client.collection("clients").document("c1").set({"name": "Acme"})
        snap = client.collection("clients").document("c1").get()
        assert snap.exists is True
        assert snap.id == "c1"
        assert snap.to_dict() == {"name": "Acme"}

    def test_set_merge_true_preserves_existing_fields(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        client.collection("clients").document("c1").set({"name": "Acme", "is_active": True})
        client.collection("clients").document("c1").set({"is_active": False}, merge=True)
        snap = client.collection("clients").document("c1").get()
        assert snap.to_dict() == {"name": "Acme", "is_active": False}

    def test_set_without_merge_replaces_document(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        client.collection("clients").document("c1").set({"name": "Acme", "is_active": True})
        client.collection("clients").document("c1").set({"name": "Acme"})
        snap = client.collection("clients").document("c1").get()
        assert snap.to_dict() == {"name": "Acme"}

    def test_persists_to_disk_across_instances(self, tmp_path):
        data_file = tmp_path / "data.json"
        LocalFirestoreClient(data_file).collection("clients").document("c1").set({"name": "Acme"})
        second_client = LocalFirestoreClient(data_file)
        snap = second_client.collection("clients").document("c1").get()
        assert snap.to_dict() == {"name": "Acme"}


class TestDocumentAutoId:
    def test_document_with_no_id_generates_unique_id(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        ref1 = client.collection("bot_activity").document()
        ref2 = client.collection("bot_activity").document()
        assert ref1.id != ref2.id
        assert len(ref1.id) > 0

    def test_auto_id_doc_is_retrievable_after_set(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        ref = client.collection("bot_activity").document()
        ref.set({"status": "sent"})
        snap = client.collection("bot_activity").document(ref.id).get()
        assert snap.to_dict() == {"status": "sent"}


class TestStream:
    def test_stream_returns_all_docs_in_collection(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        client.collection("bot_configs").document("c1").set({"enabled": True})
        client.collection("bot_configs").document("c2").set({"enabled": False})
        docs = {doc.id: doc.to_dict() for doc in client.collection("bot_configs").stream()}
        assert docs == {"c1": {"enabled": True}, "c2": {"enabled": False}}

    def test_stream_empty_collection_returns_empty_list(self, tmp_path):
        client = LocalFirestoreClient(tmp_path / "data.json")
        assert client.collection("bot_configs").stream() == []


class TestSeedFileCompatibility:
    def test_reads_existing_mock_api_data_shape(self, tmp_path):
        data_file = tmp_path / "data.json"
        data_file.write_text(json.dumps({
            "clients": {"test-client": {"name": "Test Client", "is_active": True}},
            "bot_configs": {"test-client": {"channels": [{"id": "C1"}]}},
        }))
        client = LocalFirestoreClient(data_file)

        assert client.collection("clients").document("test-client").get().to_dict()["name"] == "Test Client"
        configs = list(client.collection("bot_configs").stream())
        assert configs[0].id == "test-client"
        assert configs[0].to_dict()["channels"] == [{"id": "C1"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_local_firestore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.local_firestore'`

- [ ] **Step 3: Write the implementation**

Create `functions/shared/local_firestore.py`:

```python
"""JSON-file-backed fake Firestore client for LOCAL_MODE.

Implements just the surface functions/shared/firestore_utils.py uses:
collection(name).document(id).get()/.set(merge=)/.stream(), and
collection(name).document() (no id) for an auto-generated id. Backed by
scripts/mock-api-data.json — the same file the frontend's mock API
(scripts/mock-api.py) reads/writes, so a client or bot-config edited via the
running frontend is immediately visible to a local Cloud Function run.
"""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any


class LocalDocSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class LocalDocRef:
    def __init__(self, client: "LocalFirestoreClient", collection: str, doc_id: str):
        self._client = client
        self._collection = collection
        self._doc_id = doc_id

    @property
    def id(self) -> str:
        return self._doc_id

    def get(self) -> LocalDocSnapshot:
        data = self._client._read(self._collection, self._doc_id)
        return LocalDocSnapshot(self._doc_id, data)

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self._client._write(self._collection, self._doc_id, data, merge=merge)


class LocalCollectionRef:
    def __init__(self, client: "LocalFirestoreClient", name: str):
        self._client = client
        self._name = name

    def document(self, doc_id: str | None = None) -> LocalDocRef:
        if doc_id is None:
            doc_id = secrets.token_hex(12)
        return LocalDocRef(self._client, self._name, doc_id)

    def stream(self) -> list[LocalDocSnapshot]:
        return [
            LocalDocSnapshot(doc_id, data)
            for doc_id, data in self._client._read_all(self._name).items()
        ]


class LocalFirestoreClient:
    """Fake firestore.Client backed by a JSON file, for LOCAL_MODE."""

    def __init__(self, data_file: Path):
        self._data_file = data_file
        self._lock = threading.Lock()

    def collection(self, name: str) -> LocalCollectionRef:
        return LocalCollectionRef(self, name)

    def _load(self) -> dict[str, Any]:
        if not self._data_file.exists():
            return {}
        return json.loads(self._data_file.read_text())

    def _save(self, store: dict[str, Any]) -> None:
        self._data_file.write_text(json.dumps(store, indent=2, default=str))

    def _read(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            store = self._load()
            return store.get(collection, {}).get(doc_id)

    def _read_all(self, collection: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            store = self._load()
            return store.get(collection, {})

    def _write(self, collection: str, doc_id: str, data: dict[str, Any], merge: bool) -> None:
        with self._lock:
            store = self._load()
            coll = store.setdefault(collection, {})
            existing = coll.get(doc_id, {}) if merge else {}
            coll[doc_id] = {**existing, **data}
            self._save(store)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_local_firestore.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/local_firestore.py tests/test_local_firestore.py
git commit -m "Add JSON-backed local Firestore shim for LOCAL_MODE"
```

---

### Task 5: Wire local Firestore into `get_db()`

**Files:**
- Modify: `functions/shared/firestore_utils.py:1-20` (imports and `get_db`)
- Test: `tests/test_firestore_utils.py`

**Interfaces:**
- Consumes: `LocalFirestoreClient` from Task 4, `is_local_mode()` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_firestore_utils.py` (add `from pathlib import Path` to the existing imports at the top):

```python
class TestGetDbLocalMode:
    def test_returns_local_firestore_client_when_local_mode(self, monkeypatch, tmp_path):
        from shared.local_firestore import LocalFirestoreClient

        monkeypatch.setenv("LOCAL_MODE", "true")
        monkeypatch.setattr(firestore_utils, "_LOCAL_FIRESTORE_DATA_FILE", tmp_path / "data.json")
        firestore_utils._db = None
        try:
            db = firestore_utils.get_db()
            assert isinstance(db, LocalFirestoreClient)
        finally:
            firestore_utils._db = None

    def test_returns_real_firestore_client_when_not_local_mode(self, monkeypatch):
        from shared.local_firestore import LocalFirestoreClient

        monkeypatch.delenv("LOCAL_MODE", raising=False)
        firestore_utils._db = None
        try:
            db = firestore_utils.get_db()
            assert not isinstance(db, LocalFirestoreClient)
        finally:
            firestore_utils._db = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_firestore_utils.py -v -k TestGetDbLocalMode`
Expected: FAIL — `AttributeError: module 'shared.firestore_utils' has no attribute '_LOCAL_FIRESTORE_DATA_FILE'`

- [ ] **Step 3: Write the implementation**

In `functions/shared/firestore_utils.py`, update the imports at the top of the file to:

```python
"""Firestore operations for clients, schedules, and jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import firestore

from shared.local_firestore import LocalFirestoreClient
from shared.local_secrets import is_local_mode

logger = logging.getLogger(__name__)

_db: firestore.Client | LocalFirestoreClient | None = None
_LOCAL_FIRESTORE_DATA_FILE = Path(__file__).resolve().parents[2] / "scripts" / "mock-api-data.json"
```

Replace `get_db`:

```python
def get_db() -> firestore.Client | LocalFirestoreClient:
    global _db
    if _db is None:
        _db = LocalFirestoreClient(_LOCAL_FIRESTORE_DATA_FILE) if is_local_mode() else firestore.Client()
    return _db
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_firestore_utils.py -v`
Expected: PASS (all tests in the file — the existing autouse `_mock_firestore` fixture still patches `firestore.Client` for every other test, unaffected by this change)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/firestore_utils.py tests/test_firestore_utils.py
git commit -m "Wire local Firestore shim into get_db() for LOCAL_MODE"
```

---

### Task 6: Supabase metrics repository

**Files:**
- Create: `functions/shared/metrics_repository.py`
- Test: `tests/test_metrics_repository.py`

**Interfaces:**
- Produces: `get_account_totals(client_id: str, marketplaces: list[str], report_date: str, client_tz: ZoneInfo) -> dict` returning `{"spend": float, "ppc_sales": float, "total_sales": float}`. Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics_repository.py`:

```python
"""Tests for shared.metrics_repository — Supabase metrics backend."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared import metrics_repository


class _FakeCursor:
    def __init__(self, results_by_call):
        self._results = results_by_call
        self._call_index = 0
        self.queries: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))

    def fetchone(self):
        result = self._results[self._call_index]
        self._call_index += 1
        return result


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class TestDayBoundsUtc:
    def test_pacific_full_day(self):
        start, end = metrics_repository._day_bounds_utc("2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert start == datetime(2026, 6, 4, 7, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 5, 7, 0, tzinfo=timezone.utc)


class TestGetAccountTotals:
    def test_sums_orders_and_ads_from_supabase(self):
        cursor = _FakeCursor(results_by_call=[(1604.29,), (100.18, 347.68)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            totals = metrics_repository.get_account_totals(
                "c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"),
            )
        assert totals == {"spend": 100.18, "ppc_sales": 347.68, "total_sales": 1604.29}

    def test_orders_query_filters_by_client_marketplace_and_day_window(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            metrics_repository.get_account_totals("c1", ["US", "CA"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        orders_sql, orders_params = cursor.queries[0]
        assert "FROM orders" in orders_sql
        assert orders_params[0] == "c1"
        assert orders_params[1] == ["US", "CA"]

    def test_ads_query_filters_by_client_marketplace_and_date(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            metrics_repository.get_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        ads_sql, ads_params = cursor.queries[1]
        assert "FROM ad_campaign_metrics" in ads_sql
        assert ads_params == ("c1", ["US"], date(2026, 6, 4))

    def test_missing_rows_default_to_zero(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            totals = metrics_repository.get_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert totals == {"spend": 0.0, "ppc_sales": 0.0, "total_sales": 0.0}


class TestGetConnection:
    def test_reads_supabase_db_url_from_env(self, monkeypatch):
        metrics_repository._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = "fake-connection"
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                conn = metrics_repository._get_connection()
            assert conn == "fake-connection"
            fake_psycopg2.connect.assert_called_once_with("postgresql://fake")
        finally:
            metrics_repository._conn = None

    def test_caches_connection_across_calls(self, monkeypatch):
        metrics_repository._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = "fake-connection"
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                metrics_repository._get_connection()
                metrics_repository._get_connection()
            fake_psycopg2.connect.assert_called_once()
        finally:
            metrics_repository._conn = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_metrics_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.metrics_repository'`

- [ ] **Step 3: Write the implementation**

Create `functions/shared/metrics_repository.py`:

```python
"""Supabase (Postgres) metrics backend — stands in for BigQuery in LOCAL_MODE.

Selected via the METRICS_BACKEND env var ("bigquery", the default, or
"supabase"); the dispatch and the BigQuery implementation both live in
daily_recap/main.py, unchanged. psycopg2 is imported lazily inside
_get_connection() so a production deploy (always on the "bigquery" path)
never needs it installed at import time.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_conn = None


def _day_bounds_utc(report_date: str, client_tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC [start, end) datetimes for report_date as a full day in client_tz."""
    day = date.fromisoformat(report_date)
    start_local = datetime(day.year, day.month, day.day, tzinfo=client_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _get_connection():
    global _conn
    if _conn is None:
        import psycopg2  # lazy: only required when METRICS_BACKEND=supabase

        _conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
    return _conn


def get_account_totals(
    client_id: str, marketplaces: list[str], report_date: str, client_tz: ZoneInfo,
) -> dict:
    """Sum Spend, PPC Sales, and Total Sales for report_date across
    marketplaces, from the local Supabase orders/ad_campaign_metrics tables."""
    day_start_utc, day_end_utc = _day_bounds_utc(report_date, client_tz)
    conn = _get_connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(item_price), 0)
            FROM orders
            WHERE client_id = %s
              AND marketplace = ANY(%s)
              AND purchase_date >= %s
              AND purchase_date < %s
              AND order_status != 'Cancelled'
            """,
            (client_id, marketplaces, day_start_utc, day_end_utc),
        )
        total_sales = float(cur.fetchone()[0])

        cur.execute(
            """
            SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(sales), 0)
            FROM ad_campaign_metrics
            WHERE client_id = %s
              AND marketplace = ANY(%s)
              AND date = %s
            """,
            (client_id, marketplaces, date.fromisoformat(report_date)),
        )
        row = cur.fetchone()
        spend, ppc_sales = float(row[0]), float(row[1])

    return {"spend": spend, "ppc_sales": ppc_sales, "total_sales": total_sales}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_metrics_repository.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/metrics_repository.py tests/test_metrics_repository.py
git commit -m "Add Supabase metrics repository for LOCAL_MODE"
```

---

### Task 7: Wire the Supabase backend into `daily_recap`

**Files:**
- Modify: `functions/daily_recap/main.py:30-42` (imports), `:181-224` (`_query_account_totals`)
- Modify: `functions/daily_recap/requirements.txt`
- Test: `tests/test_daily_recap.py`

**Interfaces:**
- Consumes: `metrics_repository.get_account_totals(...)` from Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daily_recap.py` (place after the `TestDayBounds` class; `MagicMock`, `patch`, and `ZoneInfo` are already imported at the top of this file):

```python
class TestQueryAccountTotalsBackendDispatch:
    def test_supabase_backend_delegates_to_metrics_repository(self, monkeypatch):
        from daily_recap.main import AccountTotals, _query_account_totals

        monkeypatch.setenv("METRICS_BACKEND", "supabase")
        try:
            with patch(
                "daily_recap.main.metrics_repository.get_account_totals",
                return_value={"spend": 1.0, "ppc_sales": 2.0, "total_sales": 3.0},
            ) as mock_get:
                totals = _query_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
            assert totals == AccountTotals(spend=1.0, ppc_sales=2.0, total_sales=3.0)
            mock_get.assert_called_once_with("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        finally:
            monkeypatch.delenv("METRICS_BACKEND", raising=False)

    def test_default_backend_still_uses_bigquery(self, monkeypatch):
        from daily_recap.main import _query_account_totals

        monkeypatch.delenv("METRICS_BACKEND", raising=False)
        with (
            patch("daily_recap.main._get_bq", return_value=MagicMock()) as mock_bq,
            patch("daily_recap.main._query_orders_total", return_value={"total_sales": 5.0}),
            patch("daily_recap.main._query_ads_total", return_value={"spend": 1.0, "ppc_sales": 2.0}),
        ):
            totals = _query_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert totals.total_sales == 5.0
        mock_bq.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=functions python -m pytest tests/test_daily_recap.py -v -k TestQueryAccountTotalsBackendDispatch`
Expected: FAIL — `AttributeError: <module 'daily_recap.main'> does not have the attribute 'metrics_repository'`

- [ ] **Step 3: Write the implementation**

In `functions/daily_recap/main.py`, add the import (after `from shared.firestore_utils import ...`):

```python
from shared import metrics_repository
```

`_query_account_totals`'s docstring and everything from `dataset = os.environ.get("BQ_DATASET", "")` onward stay exactly as they are today — do not edit or reproduce them. Insert exactly this new block as the first statement inside the function, immediately after the closing `"""` of its docstring and before the existing `dataset = os.environ.get("BQ_DATASET", "")` line:

```python
    if os.environ.get("METRICS_BACKEND", "bigquery") == "supabase":
        totals = metrics_repository.get_account_totals(client_id, marketplaces, report_date, client_tz)
        return AccountTotals(**totals)

```

(Note the blank line at the end, before `dataset = ...` — this is a pure insertion: the docstring above it and the BigQuery code below it are untouched.)

Add `psycopg2-binary==2.*` to `functions/daily_recap/requirements.txt` (new line, alongside the existing entries) — needed only when `METRICS_BACKEND=supabase` is actually exercised at runtime, per the lazy-import note in Task 6.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=functions python -m pytest tests/test_daily_recap.py -v`
Expected: PASS (all tests in the file — the existing `TestHandlerDelivery`/`TestDayBounds`/etc. tests are unaffected since they patch `_query_account_totals` or its BigQuery callees directly)

- [ ] **Step 5: Commit**

```bash
git add functions/daily_recap/main.py functions/daily_recap/requirements.txt tests/test_daily_recap.py
git commit -m "Add Supabase backend dispatch to daily_recap"
```

---

### Task 8: Supabase seed script

**Files:**
- Create: `scripts/seed-supabase.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone operational script, matching the existing `scripts/seed-firestore.py` — no unit test, per that established convention).

- [ ] **Step 1: Write the script**

Create `scripts/seed-supabase.py`:

```python
#!/usr/bin/env python3
"""Create the local metrics schema in Supabase and seed one demo day for
test-client/US, so `daily_recap` run locally has real numbers to post.

Usage:
    SUPABASE_DB_URL=postgresql://... python scripts/seed-supabase.py [client_id]

Requires: psycopg2-binary
    pip install psycopg2-binary
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    client_id text NOT NULL,
    marketplace text NOT NULL,
    purchase_date timestamptz NOT NULL,
    item_price numeric NOT NULL,
    order_status text NOT NULL DEFAULT 'Shipped'
);

CREATE TABLE IF NOT EXISTS ad_campaign_metrics (
    client_id text NOT NULL,
    marketplace text NOT NULL,
    date date NOT NULL,
    campaign_id text NOT NULL,
    cost numeric NOT NULL,
    sales numeric NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
"""


def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        sys.exit(1)

    client_id = sys.argv[1] if len(sys.argv) > 1 else "test-client"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)

            cur.execute("DELETE FROM orders WHERE client_id = %s", (client_id,))
            orders = [
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 14, 30, tzinfo=timezone.utc), 45.99, "Shipped"),
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 16, 5, tzinfo=timezone.utc), 129.50, "Shipped"),
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 20, 15, tzinfo=timezone.utc), 22.00, "Shipped"),
            ]
            cur.executemany(
                "INSERT INTO orders (client_id, marketplace, purchase_date, item_price, order_status) "
                "VALUES (%s, %s, %s, %s, %s)",
                orders,
            )

            cur.execute("DELETE FROM ad_campaign_metrics WHERE client_id = %s", (client_id,))
            campaigns = [
                (client_id, "US", yesterday, "campaign-1", 18.25, 60.00),
                (client_id, "US", yesterday, "campaign-2", 6.75, 30.00),
            ]
            cur.executemany(
                "INSERT INTO ad_campaign_metrics (client_id, marketplace, date, campaign_id, cost, sales) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                campaigns,
            )
        conn.commit()
    finally:
        conn.close()

    total_sales = sum(o[3] for o in orders)
    total_spend = sum(c[4] for c in campaigns)
    total_ppc_sales = sum(c[5] for c in campaigns)
    print(f"Seeded Supabase for client_id={client_id!r}, date={yesterday.isoformat()}")
    print(f"  Total Sales: ${total_sales:.2f}")
    print(f"  Spend:       ${total_spend:.2f}")
    print(f"  PPC Sales:   ${total_ppc_sales:.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify manually**

This is an operational script against a real external database (matching the existing `scripts/seed-firestore.py`, which also has no unit test) — it can't be exercised in this session without a real `SUPABASE_DB_URL`. Verification happens in Task 11's end-to-end run. Confirm only that the file parses:

Run: `python -c "import ast; ast.parse(open('scripts/seed-supabase.py').read())"`
Expected: no output (parses cleanly)

- [ ] **Step 3: Commit**

```bash
git add scripts/seed-supabase.py
git commit -m "Add Supabase seed script for local daily_recap demo data"
```

---

### Task 9: Local secret manager script + Makefile target

**Files:**
- Create: `scripts/local-secret-manager.sh`
- Modify: `Makefile`

**Interfaces:**
- Consumes: nothing from earlier tasks (writes `scripts/local-secrets.json`, which Task 1's `local_secrets.py` reads).

- [ ] **Step 1: Write the script**

Create `scripts/local-secret-manager.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$SCRIPT_DIR/local-secrets.json"

# On Windows, a bare `python3` on PATH can be a non-functional Windows Store
# shim that prints an install prompt instead of running. Pick the first
# interpreter that actually executes.
PY=python3
"$PY" --version >/dev/null 2>&1 || PY=python
"$PY" --version >/dev/null 2>&1 || PY=py

if [[ ! -f "$FILE" ]]; then
  echo "{}" > "$FILE"
fi

ACTION="${1:-}"
NAME="${2:-}"
VALUE="${3:-}"

case "$ACTION" in
  set)
    if [[ -z "$NAME" || -z "$VALUE" ]]; then
      echo "Usage: make local-secret-set NAME=x VALUE=y" >&2
      exit 1
    fi
    "$PY" - "$FILE" "$NAME" "$VALUE" <<'PYEOF'
import json, sys
file, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(file) as f:
    data = json.load(f)
data[name] = value
with open(file, "w") as f:
    json.dump(data, f, indent=2)
print(f"Set local secret '{name}'")
PYEOF
    ;;
  get)
    "$PY" - "$FILE" "$NAME" <<'PYEOF'
import json, sys
file, name = sys.argv[1], sys.argv[2]
with open(file) as f:
    data = json.load(f)
if name not in data:
    print(f"'{name}' not found", file=sys.stderr)
    sys.exit(1)
print(data[name])
PYEOF
    ;;
  list)
    "$PY" - "$FILE" <<'PYEOF'
import json, sys
file = sys.argv[1]
with open(file) as f:
    data = json.load(f)
for k in sorted(data):
    print(k)
PYEOF
    ;;
  *)
    echo "Usage: local-secret-manager.sh {set|get|list} NAME [VALUE]" >&2
    exit 1
    ;;
esac
```

- [ ] **Step 2: Add the Makefile target**

In `Makefile`, add under the `# --- Secrets ---` section (after the existing `secret-list` target):

```makefile
local-secret-set:  ## Set a local secret (LOCAL_MODE). Usage: make local-secret-set NAME=x VALUE=y
	@./scripts/local-secret-manager.sh set $(NAME) $(VALUE)

local-secret-get:  ## Get a local secret value. Usage: make local-secret-get NAME=x
	@./scripts/local-secret-manager.sh get $(NAME)

local-secret-list:  ## List all local secret names
	@./scripts/local-secret-manager.sh list
```

Add the three new target names to the `.PHONY` line alongside the existing `secret-set secret-get secret-list`.

- [ ] **Step 3: Verify manually**

Run: `bash scripts/local-secret-manager.sh set kalilos-staging-slack-bot-token xoxb-test-value`
Expected: `Set local secret 'kalilos-staging-slack-bot-token'`, and `scripts/local-secrets.json` now contains that key.

Run: `bash scripts/local-secret-manager.sh get kalilos-staging-slack-bot-token`
Expected: `xoxb-test-value`

- [ ] **Step 4: Commit**

```bash
git add scripts/local-secret-manager.sh Makefile
git commit -m "Add local secret manager script and Makefile targets"
```

---

### Task 10: Wiring — `.env.local`, `run-local.sh`, Makefile runner, `.gitignore`

**Files:**
- Create: `.env.local.example`
- Modify: `scripts/run-local.sh`
- Modify: `Makefile`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing new (ties together Tasks 1–9's env-var contracts: `LOCAL_MODE`, `METRICS_BACKEND`, `SUPABASE_DB_URL`).

- [ ] **Step 1: Create the env template**

Create `.env.local.example`:

```
LOCAL_MODE=true
METRICS_BACKEND=supabase
SUPABASE_DB_URL=postgresql://postgres:[password]@[host]:5432/postgres
GCP_PROJECT=local
ENVIRONMENT=staging
BQ_DATASET=unused
```

- [ ] **Step 2: Update `run-local.sh`**

In `scripts/run-local.sh`, after the existing block that sources `.env.staging`:

```bash
# Load staging env for local development
ENV_FILE="$PROJECT_ROOT/.env.staging"
if [[ -f "$ENV_FILE" ]]; then
  log "Loading environment from $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
```

add:

```bash
# Load local-mode overrides (LOCAL_MODE, METRICS_BACKEND, SUPABASE_DB_URL,
# etc.) on top of .env.staging, if present.
LOCAL_ENV_FILE="$PROJECT_ROOT/.env.local"
if [[ -f "$LOCAL_ENV_FILE" ]]; then
  log "Loading local overrides from $LOCAL_ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV_FILE"
  set +a
fi
```

- [ ] **Step 3: Add the Makefile target**

In `Makefile`, under `# --- Local Development ---` (after the existing `local-mcp-http` target):

```makefile
local-daily-recap:  ## Run daily recap locally against Supabase + real Slack (LOCAL_MODE)
	@$(MAKE) local-fn NAME=daily_recap PORT=8082
```

Add `local-daily-recap` to the `.PHONY` line alongside the existing `local-fn local-frontend local-mcp local-mcp-http`.

- [ ] **Step 4: Update `.gitignore`**

In `.gitignore`, under the existing `# Environment (secrets)` section:

```
# Environment (secrets)
.env
.env.staging
.env.prod
frontend/.env
.env.local
```

Add a new section:

```
# Local dev sandbox (LOCAL_MODE) — dev-only data, not secrets but not
# meant to be committed either
scripts/local-secrets.json
scripts/mock-api-data.json
```

- [ ] **Step 5: Verify manually**

Run: `cp .env.local.example .env.local` then edit in a real `SUPABASE_DB_URL` and confirm `git status` does **not** show `.env.local` as untracked (it's ignored).

Run: `bash scripts/run-local.sh daily_recap 8082` in one terminal (or `make local-daily-recap` if `make` is available) — confirm the log line `Loading local overrides from .../.env.local` appears, and the server starts on port 8082.

- [ ] **Step 6: Commit**

```bash
git add .env.local.example scripts/run-local.sh Makefile .gitignore
git commit -m "Wire LOCAL_MODE env file and local-daily-recap Makefile target"
```

---

### Task 11: End-to-end verification

**Files:** none (manual verification only).

- [ ] **Step 1: Prerequisites**

- A real Supabase project with its Postgres connection string.
- A real Slack app/bot token with permission to post in a test channel, and the bot invited to that channel.

- [ ] **Step 2: Seed Supabase**

Run: `SUPABASE_DB_URL=<your-connection-string> python scripts/seed-supabase.py test-client`
Expected: prints `Seeded Supabase for client_id='test-client', ...` with non-zero Total Sales/Spend/PPC Sales.

- [ ] **Step 3: Set the local Slack secret**

Run: `bash scripts/local-secret-manager.sh set kalilos-staging-slack-bot-token xoxb-<your-real-token>`

- [ ] **Step 4: Configure `.env.local`**

`cp .env.local.example .env.local`, fill in `SUPABASE_DB_URL`.

- [ ] **Step 5: Ensure `test-client` has `daily_recap_enabled` and a real channel**

In the running frontend (`http://localhost:5173`, from the mock API work done earlier this session), open Test Client's Bot Config dialog, enable "Daily Recap Enabled", and set at least one channel to a real Slack channel ID your bot is a member of. Save. This writes to `scripts/mock-api-data.json` — the same file the local Firestore shim reads.

- [ ] **Step 6: Run `daily_recap` locally and trigger it**

Terminal 1: `bash scripts/run-local.sh daily_recap 8082`
Terminal 2: `curl -X POST http://localhost:8082 -d '{}'`

Expected: terminal 2 prints `{"status": "ok", "messages_sent": 1, "errors": 0}`; a real recap message appears in the configured Slack channel with the numbers from Step 2.

- [ ] **Step 7: Confirm the activity log**

Run: `python -c "import json; print(json.load(open('scripts/mock-api-data.json'))['bot_activity'])"`
Expected: shows an entry with `"status": "sent"` and a `"message_ts"`.

No commit for this task — it's verification, not code.
