"""Documentation checks: docs/data_dictionary.md must stay in step with the model SQL.

These tests need no data, so they run even without the Olist CSVs in data/raw/.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DICT = ROOT / "docs" / "data_dictionary.md"
LINEAGE = ROOT / "docs" / "data_lineage.md"
MODEL_SQL = [ROOT / "sql" / f for f in ("01_staging.sql", "02_dimensions.sql", "03_facts.sql")]

CREATE_RE = re.compile(r"CREATE OR REPLACE (?:TABLE|VIEW) (\w+) AS(.*?)(?=CREATE OR REPLACE|\Z)", re.S)


def _sql_blocks() -> dict[str, str]:
    blocks: dict[str, str] = {}
    for path in MODEL_SQL:
        for name, body in CREATE_RE.findall(path.read_text(encoding="utf-8")):
            blocks[name] = body
    return blocks


def _dictionary_sections() -> dict[str, list[str]]:
    """Map '### `table`' headings to the backticked identifiers in the first column of their tables."""
    text = DICT.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    for m in re.finditer(r"^### `(\w+)`.*?$(.*?)(?=^### |^## |\Z)", text, re.S | re.M):
        cols: list[str] = []
        for line in m.group(2).splitlines():
            if line.startswith("| `"):
                first_cell = line.split("|")[1]
                cols += re.findall(r"`(\w+)`", first_cell)
        if not cols:  # prose-only section (e.g. a view listing carried columns): use every backticked name
            cols = [c for c in re.findall(r"`(\w+)`", m.group(2)) if c != m.group(1)]
        sections[m.group(1)] = cols
    return sections


def test_every_model_table_is_documented():
    text = DICT.read_text(encoding="utf-8")
    missing = [t for t in _sql_blocks() if f"`{t}`" not in text]
    assert not missing, f"tables built by the model SQL but not in docs/data_dictionary.md: {missing}"


@pytest.mark.parametrize("table", sorted(_dictionary_sections()))
def test_documented_columns_exist_in_sql(table):
    blocks = _sql_blocks()
    assert table in blocks, f"docs/data_dictionary.md documents `{table}`, which the model SQL does not build"
    body = blocks[table]
    cols = _dictionary_sections()[table]
    assert cols, f"no columns documented for `{table}`"
    missing = [c for c in cols if not re.search(rf"\b{re.escape(c)}\b", body)]
    assert not missing, f"`{table}` columns documented but not found in its SQL: {missing}"


def test_lineage_references_existing_files():
    text = LINEAGE.read_text(encoding="utf-8")
    paths = set(re.findall(r"`((?:sql|scripts|analysis|tests|reports|powerbi|docs)/[\w./-]+\.(?:sql|py|md|csv|json|m))`", text))
    missing = sorted(p for p in paths if not (ROOT / p).exists())
    assert not missing, f"docs/data_lineage.md points to files that do not exist: {missing}"
