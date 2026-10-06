"""Typed dataset catalog.

Reference data is loaded through a catalog that validates the collection name
and records a content hash, so a run pins exactly the data it read and an
uncontrolled file path cannot be constructed from a scenario field.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..state.store import StateStore
from .scenario import DatasetRef

_KNOWN = ("accounts", "policies", "incidents", "knowledge_base")


class DatasetCatalog:
    def __init__(self, datasets_dir: str | Path) -> None:
        self._dir = Path(datasets_dir)

    def load_verified(self, ref: DatasetRef) -> tuple[list[dict], str]:
        if ref.collection not in _KNOWN:
            raise ValueError(f"unknown dataset collection: {ref.collection!r}")
        raw = (self._dir / f"{ref.collection}.jsonl").read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        if ref.content_hash is not None and ref.content_hash != content_hash:
            raise ValueError(f"dataset {ref.collection} hash mismatch: "
                             f"expected {ref.content_hash}, got {content_hash}")
        records = [json.loads(ln) for ln in raw.decode().splitlines() if ln.strip()]
        return records, content_hash


def seed_store(dataset_refs: tuple[DatasetRef, ...], catalog: DatasetCatalog) -> StateStore:
    store = StateStore.empty()
    for reference in dataset_refs:
        records, _hash = catalog.load_verified(reference)
        store.load_reference_data(reference.collection, records)
    return store
