"""Shared fixtures: build a fresh model into a temp dir (also tests reproducibility) and a pandas reference."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import build_model  # noqa: E402
import reference_pandas  # noqa: E402

RAW = ROOT / "data" / "raw"


def _hash_raw() -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(RAW.glob("*.csv"))}


@pytest.fixture(scope="session", autouse=True)
def raw_files_unmodified():
    """Raw CSVs must be byte-identical before and after building/testing."""
    before = _hash_raw()
    yield before
    assert _hash_raw() == before, "raw CSV files were modified"


@pytest.fixture(scope="session")
def build_results(tmp_path_factory):
    db = tmp_path_factory.mktemp("model") / "olist_model.duckdb"
    results = build_model.build(RAW, db, verbose=False)
    return db, results


@pytest.fixture(scope="session")
def con(build_results):
    db, _ = build_results
    connection = duckdb.connect(str(db), read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def ref():
    return reference_pandas.compute_reference(RAW)
