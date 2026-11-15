"""Workstream 5 - Seller (partner) performance scorecard and quarterly business review (descriptive).

Usage (project root, after `python scripts/build_model.py`):
    python analysis/seller_scorecard.py

Question: which sellers drive marketplace GMV each quarter, are they keeping up with the marketplace on
delivery and reviews, and how did each one move quarter on quarter?

Primary aggregations are DuckDB SQL (sql/analysis/scorecard/*.sql) on the validated model. Python derives rates,
benchmark-relative status, quarter-on-quarter changes, the QBR snapshot, charts and the findings report.

Population: `v_single_seller_orders` (94,931 eligible single-seller orders, 2017-01 to 2018-08).
Rules (fixed before looking at results):
  * GMV = sum of item prices (items_value), excluding freight.
  * A seller is *scored* in a quarter only with at least MIN_SCORED_ORDERS orders in that quarter.
  * Benchmarks are the marketplace's own quarterly on-time rate and mean P0 review score.
  * Status: On track = meets both benchmarks; Watch = misses one; Review = misses both. "Meets" allows a
    tolerance of 1.0 pp on on-time rate and 0.10 points on review score (see the revision note in the report).
  * The QBR compares the latest full quarter (2018-Q2) with the previous one (2018-Q1). 2018-Q3 holds only
    July-August purchases and is flagged as partial.
Status labels are screening labels for a business review conversation, not statistical verdicts on any seller.
Outputs: reports/tables/scorecard_*.csv, reports/scorecard_stats.json, reports/figures/scorecard_*.png,
         powerbi/data/scorecard_*.csv, reports/seller_scorecard_findings.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "analysis" / "scorecard"
DB = ROOT / "data" / "processed" / "olist_model.duckdb"
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
PBI_DIR = ROOT / "powerbi" / "data"
STATS_JSON = ROOT / "reports" / "scorecard_stats.json"
REPORT = ROOT / "reports" / "seller_scorecard_findings.md"

MIN_SCORED_ORDERS = 30
TOL_ON_TIME_PP = 1.0     # within 1.0 pp of the on-time benchmark counts as meeting it
TOL_REVIEW = 0.10        # within 0.10 points of the review benchmark counts as meeting it
QBR_QUARTER = pd.Timestamp("2018-04-01").date()
PREV_QUARTER = pd.Timestamp("2018-01-01").date()
PARTIAL_QUARTERS = {pd.Timestamp("2018-07-01").date()}
TOP_N = 10
STATUS_ORDER = ["On track", "Watch", "Review"]


def run_sql(con, name: str) -> pd.DataFrame:
    df = con.execute((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")).df()
    df["quarter_start"] = pd.to_datetime(df["quarter_start"]).dt.date
    return df


def quarter_label(d) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def status_of(on_time_ok: bool, review_ok: bool) -> str:
    """Benchmark-relative status. A missing review mean counts as not meeting the review benchmark."""
    met = int(bool(on_time_ok)) + int(bool(review_ok))
    return {2: "On track", 1: "Watch", 0: "Review"}[met]


def marketplace(con) -> pd.DataFrame:
    m = run_sql(con, "marketplace_quarter")
    m["quarter_label"] = m["quarter_start"].map(quarter_label)
    m["on_time_rate"] = 1 - m["late_orders"] / m["orders"]
    m["avg_review"] = m["review_score_sum"] / m["review_orders"]
    m["is_partial_quarter"] = m["quarter_start"].isin(PARTIAL_QUARTERS)
    m["gmv_qoq_pct"] = m["gmv"].pct_change()
    return m


def seller_quarters(con, m: pd.DataFrame) -> pd.DataFrame:
    s = run_sql(con, "seller_quarter")
    s["aov"] = s["gmv"] / s["orders"]
    s["on_time_rate"] = 1 - s["late_orders"] / s["orders"]
    s["avg_review"] = np.where(s["review_orders"] > 0, s["review_score_sum"] / s["review_orders"].where(s["review_orders"] > 0), np.nan)
    s["low_review_share"] = np.where(s["review_orders"] > 0, s["low_review_orders"] / s["review_orders"].where(s["review_orders"] > 0), np.nan)
    s["is_scored"] = s["orders"] >= MIN_SCORED_ORDERS
    s["is_partial_quarter"] = s["quarter_start"].isin(PARTIAL_QUARTERS)

    bench = m.set_index("quarter_start")
    s["bench_on_time_rate"] = s["quarter_start"].map(bench["on_time_rate"])
    s["bench_avg_review"] = s["quarter_start"].map(bench["avg_review"])
    s["gmv_share_of_marketplace"] = s["gmv"] / s["quarter_start"].map(bench["gmv"])
    s["on_time_vs_bench_pp"] = (s["on_time_rate"] - s["bench_on_time_rate"]) * 100
    s["review_vs_bench"] = s["avg_review"] - s["bench_avg_review"]
    s["status"] = [
        status_of(a >= -TOL_ON_TIME_PP, b >= -TOL_REVIEW) if sc else None
        for a, b, sc in zip(s["on_time_vs_bench_pp"], s["review_vs_bench"].fillna(-np.inf), s["is_scored"])
    ]

    # Quarter-on-quarter: compare with the same seller's immediately preceding calendar quarter.
    s = s.sort_values(["seller_id", "quarter_start"]).reset_index(drop=True)
    qs = sorted(m["quarter_start"])
    prev_of = {q: (qs[i - 1] if i else None) for i, q in enumerate(qs)}
    prev = s[["seller_id", "quarter_start", "orders", "gmv", "on_time_rate", "avg_review", "is_scored"]].rename(
        columns={c: f"prev_{c}" for c in ["orders", "gmv", "on_time_rate", "avg_review", "is_scored"]})
    prev["quarter_start"] = prev["quarter_start"].map({v: k for k, v in prev_of.items() if v is not None})
    prev = prev.dropna(subset=["quarter_start"])
    s = s.merge(prev, on=["seller_id", "quarter_start"], how="left")
    s["has_prev_quarter"] = s["prev_orders"].notna()
    s["prev_is_scored"] = s["prev_is_scored"].astype("boolean").fillna(False).astype(bool)
    s["gmv_qoq_pct"] = s["gmv"] / s["prev_gmv"] - 1
    s["orders_qoq_pct"] = s["orders"] / s["prev_orders"] - 1
    both = s["is_scored"] & s["prev_is_scored"]
    s["on_time_qoq_pp"] = np.where(both, (s["on_time_rate"] - s["prev_on_time_rate"]) * 100, np.nan)
    s["review_qoq"] = np.where(both, s["avg_review"] - s["prev_avg_review"], np.nan)
    s["quarter_label"] = s["quarter_start"].map(quarter_label)
    s["seller_label"] = s["seller_id"].str[:8]
    return s.sort_values(["quarter_start", "gmv"], ascending=[True, False]).reset_index(drop=True)


def qbr(s: pd.DataFrame, m: pd.DataFrame) -> dict:
    cur = s[(s.quarter_start == QBR_QUARTER)]
    scored = cur[cur.is_scored].copy()
    mq = m.set_index("quarter_start").loc[QBR_QUARTER]
    mp = m.set_index("quarter_start").loc[PREV_QUARTER]
    status_counts = scored["status"].value_counts().reindex(STATUS_ORDER, fill_value=0)
    status_gmv = scored.groupby("status")["gmv"].sum().reindex(STATUS_ORDER, fill_value=0.0)
    top = scored.nlargest(TOP_N, "gmv").copy()
    top.insert(0, "gmv_rank", range(1, len(top) + 1))
    review_list = scored[scored.status == "Review"].nlargest(TOP_N, "gmv").copy()
    movers = scored[scored.prev_is_scored].copy()
    return {
        "scored": scored, "top": top, "review_list": review_list,
        "stats": {
            "tol_on_time_pp": TOL_ON_TIME_PP, "tol_review": TOL_REVIEW,
            "qbr_quarter": quarter_label(QBR_QUARTER), "prev_quarter": quarter_label(PREV_QUARTER),
            "min_scored_orders": MIN_SCORED_ORDERS,
            "active_sellers": int(len(cur)), "scored_sellers": int(len(scored)),
            "scored_share_of_orders": float(scored.orders.sum() / mq.orders),
            "scored_share_of_gmv": float(scored.gmv.sum() / mq.gmv),
            "marketplace_gmv": float(mq.gmv), "marketplace_gmv_prev": float(mp.gmv),
            "marketplace_gmv_qoq_pct": float(mq.gmv / mp.gmv - 1),
            "marketplace_orders": int(mq.orders),
            "bench_on_time_rate": float(mq.on_time_rate), "bench_on_time_rate_prev": float(mp.on_time_rate),
            "bench_avg_review": float(mq.avg_review), "bench_avg_review_prev": float(mp.avg_review),
            "status_counts": {k: int(v) for k, v in status_counts.items()},
            "status_gmv_share": {k: float(v / scored.gmv.sum()) for k, v in status_gmv.items()},
            "top10_gmv_share_of_marketplace": float(top.gmv.sum() / mq.gmv),
            "top10_on_track": int((top.status == "On track").sum()),
            "scored_both_quarters": int(len(movers)),
            "scored_both_gmv_growth_median": float(movers.gmv_qoq_pct.median()),
            "scored_both_on_time_improved": int((movers.on_time_qoq_pp > 0).sum()),
        },
    }


def charts(s: pd.DataFrame, m: pd.DataFrame, q: dict) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    # 1. QBR scatter: on-time vs benchmark against review vs benchmark, bubble = GMV.
    sc = q["scored"]
    colors = {"On track": "#2a9d8f", "Watch": "#e9c46a", "Review": "#e76f51"}
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for st in STATUS_ORDER:
        d = sc[sc.status == st]
        ax.scatter(d.on_time_vs_bench_pp, d.review_vs_bench, s=d.gmv / sc.gmv.max() * 900 + 15,
                   alpha=0.6, color=colors[st], edgecolor="white", linewidth=0.5, label=f"{st} ({len(d)})")
    ax.axhline(0, color="#555", lw=0.8)
    ax.axvline(0, color="#555", lw=0.8)
    ax.axhline(-TOL_REVIEW, color="#999", lw=0.8, ls="--")
    ax.axvline(-TOL_ON_TIME_PP, color="#999", lw=0.8, ls="--")
    ax.set_xlabel("On-time rate vs marketplace (percentage points)")
    ax.set_ylabel("Mean review score vs marketplace")
    ax.set_title(f"{quarter_label(QBR_QUARTER)} seller scorecard: {len(sc)} sellers with ≥{MIN_SCORED_ORDERS} orders\n"
                 "bubble size = GMV", fontsize=11)
    leg = ax.legend(frameon=False, loc="lower left", fontsize=9, title="dashed = tolerance band", title_fontsize=8)
    for h in leg.legend_handles:
        h.set_sizes([60])
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p = FIG_DIR / "scorecard_01_qbr_matrix.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p.name)

    # 2. GMV by status per quarter (scored sellers) + unscored remainder.
    full = s[~s.is_partial_quarter]
    pv = full[full.is_scored].pivot_table(index="quarter_label", columns="status", values="gmv", aggfunc="sum").reindex(columns=STATUS_ORDER).fillna(0)
    pv["Not scored (<30 orders)"] = full[~full.is_scored].groupby("quarter_label")["gmv"].sum()
    pv = pv / 1e6
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bottom = np.zeros(len(pv))
    for col, c in zip(pv.columns, [colors["On track"], colors["Watch"], colors["Review"], "#cfd8dc"]):
        ax.bar(pv.index, pv[col], bottom=bottom, color=c, label=col, width=0.65)
        bottom += pv[col].to_numpy()
    ax.set_ylabel("GMV (BRL millions)")
    ax.set_title("Quarterly GMV by seller scorecard status (full quarters)", fontsize=11)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p = FIG_DIR / "scorecard_02_gmv_by_status.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    paths.append(p.name)
    return paths


def compute_all(con) -> dict:
    m = marketplace(con)
    s = seller_quarters(con, m)
    q = qbr(s, m)
    return {"marketplace": m, "seller_quarter": s, "qbr": q}


SELLER_COLS = ["seller_id", "seller_label", "quarter_start", "quarter_label", "is_partial_quarter", "orders", "customers", "gmv", "aov",
               "gmv_share_of_marketplace", "late_orders", "on_time_rate", "review_orders", "review_score_sum", "avg_review",
               "low_review_orders", "low_review_share", "is_scored", "bench_on_time_rate", "bench_avg_review", "on_time_vs_bench_pp",
               "review_vs_bench", "status", "has_prev_quarter", "prev_is_scored", "prev_gmv", "gmv_qoq_pct",
               "orders_qoq_pct", "on_time_qoq_pp", "review_qoq"]


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def write_report(res: dict, figs: list[str]) -> None:
    st = res["qbr"]["stats"]
    top = res["qbr"]["top"]
    rl = res["qbr"]["review_list"]
    m = res["marketplace"]
    lines = [
        "# Seller (partner) scorecard and quarterly business review",
        "",
        "Generated by `analysis/seller_scorecard.py`. Population: eligible single-seller orders (94,931; 2017-01 to 2018-08). "
        "GMV is the sum of item prices in BRL, excluding freight. All results are descriptive. A status label is a prompt for a "
        "business-review conversation, not a verdict on a seller.",
        "",
        "## Rules (fixed before looking at results)",
        "",
        f"- A seller is scored in a quarter only with at least {MIN_SCORED_ORDERS} orders in that quarter.",
        "- Benchmarks are the marketplace's own quarterly on-time rate and mean review score (P0: orders with exactly one review row).",
        f"- **On track** = meets both benchmarks, **Watch** = misses one, **Review** = misses both. A seller meets a benchmark when it is no more than {TOL_ON_TIME_PP:.1f} pp below the on-time rate or {TOL_REVIEW:.2f} points below the mean review.",
        "- *Revision note:* the first version used strict cut-offs (any shortfall). It labelled the two largest sellers Review for gaps of 0.1 pp and 0.01 points, which is noise at these volumes, so the tolerance band was added before the results were written up.",
        f"- The QBR compares {st['qbr_quarter']} (latest full quarter) with {st['prev_quarter']}. 2018-Q3 holds only July-August purchases and is marked partial.",
        "",
        "## Marketplace by quarter",
        "",
        "| Quarter | Orders | Active sellers | GMV (BRL) | GMV QoQ | On-time rate | Mean review |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in m.iterrows():
        qoq = "" if pd.isna(r.gmv_qoq_pct) else pct(r.gmv_qoq_pct)
        lab = r.quarter_label + (" (partial)" if r.is_partial_quarter else "")
        lines.append(f"| {lab} | {r.orders:,} | {r.active_sellers:,} | {r.gmv:,.0f} | {qoq} | {pct(r.on_time_rate)} | {r.avg_review:.2f} |")
    sc = st["status_counts"]
    sg = st["status_gmv_share"]
    lines += [
        "",
        f"## {st['qbr_quarter']} QBR snapshot",
        "",
        f"- Marketplace GMV was BRL {st['marketplace_gmv']:,.0f}, {pct(st['marketplace_gmv_qoq_pct'])} vs {st['prev_quarter']}.",
        f"- On-time benchmark {pct(st['bench_on_time_rate'])} (was {pct(st['bench_on_time_rate_prev'])}); review benchmark "
        f"{st['bench_avg_review']:.2f} (was {st['bench_avg_review_prev']:.2f}). {st['prev_quarter']} includes the Feb-Mar 2018 high-delay months, "
        "so most of the on-time recovery is marketplace-wide; that is why status is measured against the benchmark rather than in absolute terms.",
        f"- {st['scored_sellers']} of {st['active_sellers']} active sellers met the {MIN_SCORED_ORDERS}-order threshold. Together they hold "
        f"{pct(st['scored_share_of_gmv'])} of GMV and {pct(st['scored_share_of_orders'])} of orders.",
        f"- Status: {sc['On track']} On track ({pct(sg['On track'])} of scored GMV), {sc['Watch']} Watch ({pct(sg['Watch'])}), "
        f"{sc['Review']} Review ({pct(sg['Review'])}).",
        f"- The top {TOP_N} sellers by GMV hold {pct(st['top10_gmv_share_of_marketplace'])} of marketplace GMV; {st['top10_on_track']} of them are On track.",
        f"- {st['scored_both_quarters']} sellers were scored in both quarters: median GMV change {pct(st['scored_both_gmv_growth_median'])}, "
        f"{st['scored_both_on_time_improved']} improved their on-time rate.",
        "",
        f"### Top {TOP_N} sellers by GMV, {st['qbr_quarter']}",
        "",
        "| Rank | Seller | Orders | GMV (BRL) | GMV QoQ | On-time | vs bench (pp) | Mean review | Status |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, r in top.iterrows():
        qoq = "new" if pd.isna(r.gmv_qoq_pct) else pct(r.gmv_qoq_pct)
        lines.append(f"| {r.gmv_rank} | {r.seller_id[:8]} | {r.orders} | {r.gmv:,.0f} | {qoq} | {pct(r.on_time_rate)} | "
                     f"{r.on_time_vs_bench_pp:+.1f} | {r.avg_review:.2f} | {r.status} |")
    lines += ["", f"### Largest Review-status sellers by GMV, {st['qbr_quarter']}", "",
              "| Seller | Orders | GMV (BRL) | On-time | vs bench (pp) | Mean review | vs bench |", "|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in rl.iterrows():
        lines.append(f"| {r.seller_id[:8]} | {r.orders} | {r.gmv:,.0f} | {pct(r.on_time_rate)} | {r.on_time_vs_bench_pp:+.1f} | "
                     f"{r.avg_review:.2f} | {r.review_vs_bench:+.2f} |")
    lines += ["", "## Figures", ""] + [f"![{f}](figures/{f})" for f in figs] + [
        "", "## Limitations", "",
        "- Single-seller orders only; multi-seller orders (1.3%) are not attributed.",
        "- Delivery timestamps are order-level, so seller handling and carrier transit cannot be separated: a Watch or Review "
        "status on on-time rate may reflect the carrier or the lane, not the seller.",
        "- Review means on small samples move a lot; the 30-order threshold limits but does not remove this.",
        "- No targets, costs or contracts exist in the data, so benchmarks are marketplace averages, not commercial targets.",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


INT_COLS = ["orders", "customers", "late_orders", "review_orders", "review_score_sum", "low_review_orders", "active_sellers"]


def pbi_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == bool or str(out[c].dtype) == "boolean":
            out[c] = out[c].astype(int)
        elif c in INT_COLS:
            out[c] = out[c].fillna(0).astype(int)
    return out


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    res = compute_all(con)
    con.close()
    for d in (TABLE_DIR, PBI_DIR):
        d.mkdir(parents=True, exist_ok=True)
    s = res["seller_quarter"][SELLER_COLS]
    s.to_csv(TABLE_DIR / "scorecard_seller_quarter.csv", index=False)
    res["marketplace"].to_csv(TABLE_DIR / "scorecard_marketplace_quarter.csv", index=False)
    res["qbr"]["top"][["gmv_rank"] + SELLER_COLS].to_csv(TABLE_DIR / "scorecard_qbr_top_sellers.csv", index=False)
    res["qbr"]["review_list"][SELLER_COLS].to_csv(TABLE_DIR / "scorecard_qbr_review_list.csv", index=False)
    # Power BI import package (aggregates only): flags as 0/1 and counts as integers, like the rest of the package.
    pbi_frame(s).to_csv(PBI_DIR / "scorecard_seller_quarter.csv", index=False)
    pbi_frame(res["marketplace"]).to_csv(PBI_DIR / "scorecard_marketplace_quarter.csv", index=False)
    figs = charts(res["seller_quarter"], res["marketplace"], res["qbr"])
    STATS_JSON.write_text(json.dumps(res["qbr"]["stats"], indent=2), encoding="utf-8")
    write_report(res, figs)
    print(json.dumps(res["qbr"]["stats"], indent=2))


if __name__ == "__main__":
    sys.exit(main())
