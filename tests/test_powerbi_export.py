"""Validation of the Power BI import package (scripts/export_powerbi.py) and of the DAX measure dictionary.

The package is exported from a freshly built model into a temp directory. DAX measures are validated through Python
equivalents evaluated on the exported CSVs (scripts/powerbi_dax_equivalents.py) and compared with an independent SQL
reference on the DuckDB model, across filter-context scenarios. Documentation (DAX dictionary, README) is linted
against the actual column names so the written guide cannot drift from the data.
"""
from __future__ import annotations

import itertools
import json
import math
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from scipy.stats import binomtest

import export_powerbi as ex
import powerbi_dax_equivalents as dax

ROOT = Path(__file__).resolve().parents[1]
PB = ROOT / "powerbi"
NORTHEAST = {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"}


@pytest.fixture(scope="session")
def pkg(build_results, tmp_path_factory):
    db, _ = build_results
    out = tmp_path_factory.mktemp("powerbi") / "data"
    manifest = ex.export_all(db, out, verbose=False)
    return out, manifest


@pytest.fixture(scope="session")
def tables(pkg):
    out, _ = pkg
    return {n: pd.read_csv(out / f"{n}.csv") for n in ex.MODEL_TABLES + ex.SNAPSHOT_TABLES}


@pytest.fixture(scope="session")
def model(pkg):
    return dax.PowerBIModel(pkg[0])


# ------------------------------------------------------------------ package contents and schema
def test_package_files_and_row_counts(pkg, tables):
    out, manifest = pkg
    assert sorted(p.name for p in out.glob("*")) == sorted([f"{n}.csv" for n in ex.MODEL_TABLES + ex.SNAPSHOT_TABLES] + ["manifest.json", "data_dictionary.csv"])
    counts = {n: len(t) for n, t in tables.items()}
    assert counts["fact_orders"] == 99_441 and counts["fact_seller_orders"] == 94_931
    assert counts["dim_state"] == 27 and counts["dim_seller"] == 2_925 and counts["dim_origin_state"] == 22
    assert counts["snap_priority_candidates"] == 27 + 408 + 2_925
    assert {n: m["rows"] for n, m in manifest["tables"].items()} == counts
    for n, m in manifest["tables"].items():
        assert ex.hashlib.sha256((out / f"{n}.csv").read_bytes()).hexdigest() == m["sha256"]


def test_no_raw_source_tables_or_personal_ids_exported(pkg, tables):
    names = {p.stem for p in pkg[0].glob("*.csv")}
    assert not names & {"orders", "customers", "order_items", "reviews", "geolocation", "products", "payments", "sellers"}
    forbidden = {"customer_id", "customer_unique_id", "review_comment_message", "review_comment_title", "purchase_ts", "delivered_customer_ts", "estimated_date", "delivered_date"}
    for n, t in tables.items():
        assert not forbidden & set(t.columns), n


def test_schema_matches_data_dictionary_and_types(pkg, tables):
    for name, t in tables.items():
        spec = ex.DICT[name]
        assert list(t.columns) == [c for c, *_ in spec], name
        for col, ty, *_ in spec:
            s = t[col]
            if ty == "Date":
                pd.to_datetime(s, format="%Y-%m-%d", errors="raise")
            elif ty == "Whole number":
                nn = s.dropna()
                assert (nn == np.floor(nn)).all(), (name, col)
            elif ty == "Decimal number":
                assert pd.api.types.is_numeric_dtype(s), (name, col)
    dd = pd.read_csv(pkg[0] / "data_dictionary.csv")
    assert len(dd) == sum(len(v) for v in ex.DICT.values()) and set(dd.table) == set(ex.DICT)


def test_boolean_flags_are_zero_one(tables):
    for tname, cols in {"fact_orders": ["in_window", "is_delivery_kpi_eligible", "is_late", "is_severe_late", "review_before_delivery_flag", "is_review_p0", "is_review_p1", "is_single_seller", "ts_sequence_violation"],
                        "fact_seller_orders": ["is_cross_state", "is_late", "is_severe_late", "review_before_delivery_flag", "is_review_p0", "is_review_p1"],
                        "dim_date": ["is_in_window"]}.items():
        for c in cols:
            assert set(tables[tname][c].dropna().unique()) <= {0, 1}, (tname, c)


def test_keys_unique(tables):
    assert tables["fact_orders"].order_id.is_unique and tables["fact_seller_orders"].order_id.is_unique
    for t, k in (("dim_date", "date_key"), ("dim_state", "state_code"), ("dim_origin_state", "origin_state_code"), ("dim_seller", "seller_id"), ("snap_metadata", "key")):
        assert tables[t][k].is_unique and tables[t][k].notna().all(), t
    s = tables["snap_priority_candidates"]
    assert not s.duplicated(["snapshot_level", "segment"]).any()
    assert not tables["snap_tier_counts"].duplicated(["snapshot_level", "min_excess_late"]).any()


# ------------------------------------------------------------------ relationships
def test_relationships_are_true_one_to_many_with_full_referential_integrity(tables):
    assert len(ex.RELATIONSHIPS) == 6
    for rid, one_t, one_c, many_t, many_c in ex.RELATIONSHIPS:
        one, many = tables[one_t][one_c], tables[many_t][many_c]
        assert one.is_unique and one.notna().all(), rid                      # one side unique -> one-to-many, not many-to-many
        assert many.notna().all(), rid
        assert set(many.astype(str)) <= set(one.astype(str)) or set(pd.to_datetime(many)) <= set(pd.to_datetime(one)), rid   # no orphan foreign keys
    # every fact row matches exactly one dimension row (no fan-out): merge keeps the row count
    fo, fs = tables["fact_orders"], tables["fact_seller_orders"]
    assert len(fo.merge(tables["dim_state"], left_on="customer_state", right_on="state_code")) == len(fo)
    assert len(fs.merge(tables["dim_origin_state"], left_on="seller_state", right_on="origin_state_code")) == len(fs)
    assert len(fs.merge(tables["dim_seller"], on="seller_id")) == len(fs)
    assert len(fo.assign(d=pd.to_datetime(fo.purchase_date)).merge(tables["dim_date"].assign(d=pd.to_datetime(tables["dim_date"].date_key)), on="d")) == len(fo)


def test_relationship_graph_has_no_ambiguous_paths_or_forbidden_links():
    edges = [(r[1], r[3]) for r in ex.RELATIONSHIPS]                           # filter direction: dimension -> fact
    facts, snaps = {"fact_orders", "fact_seller_orders"}, set(ex.SNAPSHOT_TABLES)
    assert not any(a in facts and b in facts for a, b in edges)               # no relationship between the two fact tables
    assert not any(a in snaps or b in snaps for a, b in edges)                # snapshot tables are disconnected
    assert all(b in facts for _, b in edges) and not any(a in facts for a, _ in edges)    # dimensions only filter facts; facts never filter anything
    pairs = [(a, b) for a, b, *_ in [(r[1], r[3]) for r in ex.RELATIONSHIPS]]
    assert len(pairs) == len(set(pairs))                                       # at most one relationship between any two tables
    # number of directed paths between every ordered pair of tables is at most one -> no ambiguity
    def paths(src, dst, seen=()):
        return 1 if src == dst else sum(paths(b, dst, seen + (src,)) for a, b in edges if a == src and b not in seen)
    tabs = {t for e in edges for t in e}
    assert all(paths(s, d) <= 1 for s, d in itertools.permutations(tabs, 2))
    # one-to-many only, no many-to-many: every many-side column is a foreign key to a unique one-side key (checked above)


def test_date_dimension(tables):
    d = pd.to_datetime(tables["dim_date"].date_key)
    assert d.is_monotonic_increasing and (d.diff().dropna() == pd.Timedelta(days=1)).all()
    assert d.min() == pd.Timestamp("2016-09-01") and d.max() == pd.Timestamp("2018-10-31") and len(d) == 791
    for t in ("fact_orders", "fact_seller_orders"):
        pdz = pd.to_datetime(tables[t].purchase_date)
        assert pdz.min() >= d.min() and pdz.max() <= d.max()
    dd = tables["dim_date"]
    assert dd.is_in_window.sum() == 608                                       # 2017-01-01 .. 2018-08-31
    assert dd.groupby("month_label").month_sort.nunique().eq(1).all() and dd.groupby("month_sort").month_label.nunique().eq(1).all()
    assert pd.to_datetime(dd.month_start).dt.day.eq(1).all()


# ------------------------------------------------------------------ content versus the validated model
def test_flags_and_populations_match_model(tables, con):
    fo, fs = tables["fact_orders"], tables["fact_seller_orders"]
    assert int(fo.in_window.sum()) == 99_092 and int(fo.is_delivery_kpi_eligible.sum()) == 96_203
    elig = fo[fo.is_delivery_kpi_eligible == 1]
    assert int(elig.is_late.sum()) == 6_531 and int(elig.is_severe_late.sum()) == 2_860
    assert int(fo.is_review_p0.sum()) == 95_037 and int(fo.is_review_p1.sum()) == 90_103
    assert fo.fulfilment_class.value_counts().to_dict() == dict(con.execute("SELECT fulfilment_class, count(*) FROM fact_orders GROUP BY 1").fetchall())
    cls = fo[fo.in_window == 1].fulfilment_class.value_counts().to_dict()
    assert cls["cancelled_unavailable"] == 1_182 and cls["open_past_promise"] == 1_699 and cls["delivered_status_no_date"] == 8
    assert cls.get("open_not_yet_due", 0) == 0
    assert len(fs) == 94_931 and fs.seller_id.nunique() == 2_925
    # the seller fact is a subset of the order fact with identical flags (reconciliation by order_id, NOT a relationship)
    m = fs.merge(fo, on="order_id", suffixes=("_s", "_o"))
    assert len(m) == len(fs)
    assert (m.is_delivery_kpi_eligible == 1).all() and (m.is_single_seller == 1).all()
    for c in ("is_late", "is_severe_late", "is_review_p0", "is_review_p1", "customer_state", "purchase_date", "review_score", "lead_time_days"):
        a, b = m[f"{c}_s"], m[f"{c}_o"]
        assert ((a == b) | (a.isna() & b.isna())).all(), c


def test_blank_semantics(tables):
    fo = tables["fact_orders"]
    delivered_dated = fo.is_late.notna()
    assert (fo.lead_time_days.notna() == delivered_dated).all() and (fo.promise_error_days.notna() == delivered_dated).all()
    assert (fo.review_score.notna() == (fo.review_row_count == 1)).all()              # score only for exactly one review row
    assert ((fo.is_review_p0 == 1) <= (fo.review_row_count == 1)).all()
    assert (fo[fo.is_review_p1 == 1].is_review_p0 == 1).all() and (fo[fo.is_review_p1 == 1].review_before_delivery_flag == 0).all()
    assert fo[fo.is_delivery_kpi_eligible == 1].is_late.notna().all()
    assert fo.delivery_outcome.notna().sum() == delivered_dated.sum()


def test_snapshot_tables_are_the_validated_priority_outputs(tables, con):
    import operational_prioritization as op
    res = op.compute_all(con)
    s = tables["snap_priority_candidates"]
    for level in op.LEVELS:
        sub = s[s.snapshot_level == level].set_index("segment")
        src = res[f"candidates_{level}"].set_index("segment")
        assert set(sub.index) == set(src.index)
        for c in ("n_delivered", "n_late", "late_rate", "excess_late", "late_rate_lo", "late_rate_hi", "oe_s1", "excess_low", "low_ontime_rate"):
            assert np.allclose(sub.loc[src.index, c].astype(float), src[c].astype(float), equal_nan=True, rtol=1e-12, atol=1e-12), (level, c)
        assert (sub.loc[src.index, "tier"] == src.tier).all()
        assert int((sub.tier == "Investigate").sum()) == {"state": 10, "lane": 9, "seller": 8}[level]
        assert abs(float(src.excess_late.sum())) < 1e-6                              # excess over a population reference sums to zero
    assert tables["snap_priority_candidates"].tier_sort.between(1, 4).all()
    lanes = s[s.snapshot_level == "lane"]
    assert (lanes.northeast_bound == lanes.destination_state.isin(NORTHEAST).astype(int)).all()      # destination-based
    assert int(lanes[lanes.segment == "MA>SP"].northeast_bound.iloc[0]) == 0
    assert s[s.snapshot_level != "lane"].northeast_bound.isna().all()
    tc = tables["snap_tier_counts"]
    assert set(tc.min_excess_late) == {10, 20, 30} and tc[tc.is_primary == 1].min_excess_late.eq(20).all()
    ov = tables["snap_overlap"]
    assert int(ov[ov.row_type == "membership"].n_orders.sum()) == 94_931
    summ = ov[ov.row_type == "summary"].set_index("label")
    assert summ.loc["Double-counted excess (naive sum minus union)", "excess_vs_ref"] > 0


def test_snapshot_metadata_states_policy_and_scope(tables):
    meta = tables["snap_metadata"].set_index("key")
    assert meta.loc["primary_excess_threshold", "value"] == "20" and "OPERATIONAL SCREENING POLICY" in meta.loc["primary_excess_threshold", "description"]
    assert "NOT recalculated" in meta.loc["snapshot_scope", "description"] and "2 of 8" in meta.loc["seller_evidence", "description"]
    assert "DESTINATION" in meta.loc["lane_notation", "description"]


# ------------------------------------------------------------------ DAX-equivalent measures vs independent SQL
def _close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9))


def _scenarios(model):
    top = model.fact_seller_orders.seller_id.value_counts().index[0]
    mid = model.fact_seller_orders.seller_id.value_counts().index[200]
    return dax.SCENARIOS + [("Top seller", {"seller_id": [top]}), ("Mid-size seller in months 2018-01..2018-08", {"seller_id": [mid], "months": ("2018-01", "2018-08")})]


def test_dax_equivalent_measures_match_sql_in_every_scenario(model, con):
    checked = 0
    for name, ctx in _scenarios(model):
        a, b = dax.measures_orders(model.orders(**ctx)), dax.sql_reference_orders(con, **ctx)
        for k, v in b.items():
            assert _close(a[k], v), (name, k, a[k], v)
            checked += 1
        a, b = dax.measures_seller(model.seller_orders(**ctx)), dax.sql_reference_seller(con, **ctx)
        for k, v in b.items():
            assert _close(a[k], v), (name, k, a[k], v)
            checked += 1
    assert checked >= 500


def test_anchor_values_through_dax_equivalents(model):
    m = dax.measures_orders(model.orders())
    s = dax.measures_seller(model.seller_orders())
    assert (m["All orders in file"], m["Orders in window"], m["Delivered orders"], m["Late orders"], m["Severe-late orders"]) == (99_441, 99_092, 96_203, 6_531, 2_860)
    assert s["Seller: eligible orders"] == 94_931 and m["Reviewed orders (P0)"] == 95_037 and m["Reviewed orders (P1)"] == 90_103
    assert math.isclose(m["Late-delivery rate"], 6_531 / 96_203, rel_tol=1e-12) and round(m["Late-delivery rate"] * 100, 2) == 6.79
    assert math.isclose(abs(m["Late-delivery rate"] - 0.0679), 0, abs_tol=5e-5)
    assert m["Cancelled/unavailable orders"] == 1_182 and m["Open past-promise orders"] == 1_699
    assert math.isclose(m["Review coverage (any review row)"], 0.9933, abs_tol=5e-5)
    assert math.isclose(m["P0 share of delivered orders"], 95_037 / 96_203, rel_tol=1e-12)


def test_slicer_semantics_follow_the_relationship_design(model):
    base = dax.measures_orders(model.orders())
    # seller and origin-state selections have no relationship to fact_orders: order-level measures must not move
    sid = model.fact_seller_orders.seller_id.iloc[0]
    assert dax.measures_orders(model.orders(seller_id=[sid], origin_state=["SP"])) == base
    # ...but they do filter the seller fact
    assert len(model.seller_orders(seller_id=[sid])) < len(model.seller_orders())
    # a customer-state slicer filters both facts, and the seller fact is smaller by the multi-seller orders
    rj_o, rj_s = model.orders(customer_state=["RJ"]), model.seller_orders(customer_state=["RJ"])
    multi = int(((rj_o.is_delivery_kpi_eligible == 1) & (rj_o.is_single_seller == 0)).sum())
    assert len(rj_o[rj_o.is_delivery_kpi_eligible == 1]) - len(rj_s) == multi == 156
    # date slicer outside the analysis window: orders exist, but no delivery KPI population (flag excludes them)
    early = dax.measures_orders(model.orders(months=("2016-09", "2016-12")))
    assert early["All orders in file"] > 0 and early["Delivered orders"] == 0 and early["Late-delivery rate"] is None
    # the two facts never filter each other: filtering by a seller leaves the order fact's date context untouched
    assert len(model.orders(months=("2017-01", "2018-08"))) == 99_092 - int(((model.fact_orders.in_window == 1) & ~model.fact_orders.purchase_date.between("2017-01-01", "2018-08-31")).sum())


def test_dax_divide_blank_behaviour(model):
    empty = dax.measures_orders(model.orders(customer_state=["ZZ"]))
    assert empty["All orders in file"] == 0 and empty["Late-delivery rate"] is None and empty["Median lead time (days)"] is None
    assert empty["Low-score share (P0)"] is None and empty["Late-delivery rate, Wilson lower"] is None
    s = dax.measures_seller(model.seller_orders(seller_id=["none"]))
    assert s["Seller: eligible orders"] == 0 and s["Seller: late-delivery rate"] is None


@pytest.mark.parametrize("k,n", [(6531, 96203), (1495, 12310), (5, 40), (0, 30), (30, 30), (2, 67)])
def test_wilson_dax_formula_matches_scipy(k, n):
    lo, hi = dax.wilson(k, n)
    ci = binomtest(k, n).proportion_ci(method="wilson")
    assert math.isclose(lo, ci.low, abs_tol=1e-9) and math.isclose(hi, ci.high, abs_tol=1e-9)


# ------------------------------------------------------------------ documentation lint
def _dax_blocks(text: str) -> list[str]:
    return re.findall(r"```dax\n(.*?)```", text, flags=re.S)


def _measure_defs(text: str) -> dict[str, str]:
    defs: dict[str, str] = {}
    for block in _dax_blocks(text):
        current = None
        for line in block.splitlines():
            m = re.match(r"^([A-Za-z][^\[\]\n]*?) =(?: |$)", line)
            if m and not line.startswith(("VAR ", "RETURN")):
                current = m.group(1).strip()
                defs[current] = line
            elif current is not None:
                defs[current] += "\n" + line
    return defs


def test_every_measure_in_the_evaluators_is_defined_in_the_dax_dictionary(model):
    text = (PB / "dax_measures.md").read_text(encoding="utf-8")
    defs = _measure_defs(text)
    names = set(dax.measures_orders(model.orders())) | set(dax.measures_seller(model.seller_orders()))
    for n in names:
        assert n in defs, n


def test_dax_columns_exist_and_scoping_rules_hold(tables):
    text = (PB / "dax_measures.md").read_text(encoding="utf-8")
    defs = _measure_defs(text)
    assert len(defs) >= 60
    for name, body in defs.items():
        assert body.count("(") == body.count(")"), name
        for t, c in re.findall(r"\b(fact_orders|fact_seller_orders|dim_\w+|snap_\w+)\[(\w+)\]", body):
            assert t in tables and c in tables[t].columns, (name, t, c)
        if name.startswith("Seller:"):
            assert "fact_orders[" not in body, name                                  # seller measures only on the single-seller fact
        elif name.startswith(("Snapshot:", "Note:")):
            assert "fact_orders[" not in body and "fact_seller_orders[" not in body, name
        else:
            assert "fact_seller_orders[" not in body, name                           # order measures never touch the seller fact
        assert not re.search(r"estimated|delivered_date|delivery_date", body), name   # delivery dates never used as filters
    # ratios use DIVIDE; the only '/' operators are inside Wilson expressions (guarded by IF n > 0)
    for name, body in defs.items():
        cleaned = re.sub(r"\[[^\]]*\]", "[m]", re.sub(r'"[^"]*"', '""', body.split(" =", 1)[1] if " =" in body else body))
        if "/" in cleaned:
            assert "Wilson" in name and "IF ( n > 0" in cleaned, name
    for name, body in defs.items():
        if re.search(r"\bDIVIDE\b", body) is None and name.startswith(("Seller: late-delivery rate", "Late-delivery rate")):
            assert "Wilson" in name or name in ("Late-delivery rate (n >= 100)", "Seller: late rate (n >= 50)"), name


def test_readme_and_specs_are_consistent_with_the_package(pkg):
    readme = (PB / "README.md").read_text(encoding="utf-8")
    specs = (PB / "page_specs.md").read_text(encoding="utf-8")
    defs = _measure_defs((PB / "dax_measures.md").read_text(encoding="utf-8"))
    for rid, one_t, one_c, many_t, many_c in ex.RELATIONSHIPS:
        assert f"| {rid} | `{one_t}[{one_c}]` | `{many_t}[{many_c}]` |" in readme, rid
    for anchor in ("99,441", "99,092", "96,203", "6,531", "2,860", "94,931", "95,037", "90,103", "6.79"):
        assert anchor in readme, anchor
    for page in ("Page 1: Executive Overview", "Page 2: Delivery Performance", "Page 3: Customer Experience", "Page 4: Operational Priorities"):
        assert page in specs
    for phrase in ("operational screening policy", "snapshot", "not statistical significance"):
        assert phrase in specs + readme
    assert "No `.pbix` file" in readme
    # every measure named in backticks in the specs exists in the dictionary (names that look like measures)
    candidates = set(re.findall(r"`([A-Z][^`\[\]]*)`", specs))
    measure_like = {c for c in candidates if c in defs or c.startswith(("Seller:", "Snapshot:", "Note:")) and not c.endswith(" >= 100")}
    assert measure_like <= set(defs)
    unknown_capitalised = {c for c in candidates if c not in defs and not c.startswith(("Seller:", "Snapshot:", "Note:"))}
    allowed = {"Investigate", "Watch", "No Signal", "On time", "Late", "Delivered orders < 100"}
    assert not {c for c in unknown_capitalised if c not in allowed and re.search(r"(orders|rate|time|score|share|error)", c)}, unknown_capitalised


def test_power_query_covers_every_table_with_typed_columns(pkg):
    text = (pkg[0].parent / "power_query.m").read_text(encoding="utf-8")
    for name in ex.MODEL_TABLES + ex.SNAPSHOT_TABLES:
        block = text.split(f"query name: {name} ")[1].split("// ----------------")[0]
        assert f'"{name}.csv"' in block and '"en-US"' in block
        for col, ty, *_ in ex.DICT[name]:
            assert f'{{"{col}", {ex.M_TYPE[ty]}}}' in block, (name, col)
    assert text.count("Table.TransformColumnTypes") == 10 and "Encoding = 65001" in text


def test_export_is_deterministic(build_results, pkg, tmp_path):
    db, _ = build_results
    m2 = ex.export_all(db, tmp_path / "again", verbose=False)
    assert {k: v["sha256"] for k, v in m2["tables"].items()} == {k: v["sha256"] for k, v in pkg[1]["tables"].items()}
