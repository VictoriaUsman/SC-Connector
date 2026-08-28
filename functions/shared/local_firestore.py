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
