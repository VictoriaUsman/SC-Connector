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
