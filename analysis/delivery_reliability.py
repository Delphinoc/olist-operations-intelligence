"""Workstream 1 - Delivery reliability: SQL KPIs (DuckDB) + statistics + charts.

Usage (project root, after `python scripts/build_model.py`):
    python analysis/delivery_reliability.py

Primary KPIs are computed in SQL (sql/analysis/delivery/*.sql) against the validated model; Python adds
Wilson / Newcombe intervals, seeded bootstrap intervals, standardisation and the charts. Nothing here
redefines the model's populations: the base population is `is_delivery_kpi_eligible` (delivered with a
delivery date, purchases 2017-01..2018-08) and fulfilment outcomes use all `in_window` orders.
Outputs: reports/tables/delivery_*.csv, reports/delivery_reliability_stats.json, reports/figures/delivery_*.png.
All associations are observational; nothing here supports a causal claim.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "analysis" / "delivery"
DB = ROOT / "data" / "processed" / "olist_model.duckdb"
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
STATS_JSON = ROOT / "reports" / "delivery_reliability_stats.json"

Z95 = 1.959963984540054
SEED = 20240901
HIGH_VOLUME_SHARE = 0.25          # pre-specified: top quartile of cohort months by delivered-order count
CLASSES = ["delivered_on_time", "delivered_late", "cancelled_unavailable", "open_past_promise",
           "open_not_yet_due", "delivered_status_no_date"]
BASE = dict(end_excl=dt.date(2018, 9, 1), excl_anom=False, maturity_days=0)
VARIANTS = {   # label -> parameter overrides (documented in the blueprint / task)
    "Base: purchases 2017-01..2018-08": {},
    "Window ends 2018-07": dict(end_excl=dt.date(2018, 8, 1)),
    "Cohort maturity 0 days (= base)": dict(maturity_days=0),
    "Cohort maturity 30 days": dict(maturity_days=30),
    "Cohort maturity 60 days": dict(maturity_days=60),
    "Excluding timestamp-sequence anomalies": dict(excl_anom=True),
}


# ------------------------------------------------------------------ statistics helpers
def wilson(k, n, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (nan, nan) when n == 0."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_diff(k1, n1, k2, n2, z: float = Z95) -> tuple[float, float, float]:
    """Newcombe (method 10) interval for p1 - p2 built from the two Wilson intervals."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    d = p1 - p2
    return (d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def add_wilson(df: pd.DataFrame, k: str, n: str, prefix: str) -> pd.DataFrame:
    df[prefix] = df[k] / df[n]
    bounds = [wilson(a, b) for a, b in zip(df[k], df[n])]
    df[prefix + "_lo"] = [b[0] for b in bounds]
    df[prefix + "_hi"] = [b[1] for b in bounds]
    return df


def bootstrap_quantiles(x: np.ndarray, qs: tuple[float, ...], n_boot: int, rng: np.random.Generator) -> np.ndarray:
    """Percentile bootstrap (2.5%, 97.5%) for each quantile in qs. Returns array shape (len(qs), 2)."""
    n = len(x)
    out = np.empty((n_boot, len(qs)))
    for b in range(n_boot):
        out[b] = np.quantile(x[rng.integers(0, n, n)], qs)
    return np.percentile(out, [2.5, 97.5], axis=0).T


# ------------------------------------------------------------------ SQL access
def run_sql(con: duckdb.DuckDBPyConnection, name: str, params: dict | None = None) -> pd.DataFrame:
    sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    df = con.execute(sql, params).df() if params else con.execute(sql).df()
    if "purchase_month" in df.columns:          # python dates keep merges and comparisons unambiguous
        df["purchase_month"] = pd.to_datetime(df["purchase_month"]).dt.date
    return df


# ------------------------------------------------------------------ main computation
def compute_all(con: duckdb.DuckDBPyConnection, n_boot_overall: int = 1000, n_boot_month: int = 500,
                seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    res: dict = {}
    ref_date = con.execute("SELECT reference_date FROM model_params").fetchone()[0]
    res["reference_date"] = ref_date

    # (1) monthly late rate with Wilson intervals --------------------------------------------------
    m = run_sql(con, "monthly_kpis")
    m = add_wilson(m, "n_late", "n_delivered", "late_rate")
    m = add_wilson(m, "n_severe", "n_delivered", "severe_rate")
    m["late_exact_rate"] = m.n_late_exact / m.n_delivered
    last_day = pd.to_datetime(m.purchase_month) + pd.offsets.MonthEnd(0)
    for days in (30, 60):
        m[f"mature_{days}d"] = (last_day + pd.Timedelta(days=days)).dt.date <= ref_date
    m["volume_rank"] = m.n_delivered.rank(ascending=False, method="first").astype(int)
    n_high = math.ceil(HIGH_VOLUME_SHARE * len(m))
    m["high_volume"] = m.volume_rank <= n_high

    # (2) lead-time quantiles with seeded bootstrap intervals -----------------------------------------
    lt = run_sql(con, "lead_time_orders")
    x_all = lt.lead_time_days.to_numpy()
    qs = (0.5, 0.9, 0.95)
    ci = bootstrap_quantiles(x_all, qs, n_boot_overall, rng)
    res["lead_overall"] = {
        "n": int(len(x_all)),
        **{f"{name}": float(np.quantile(x_all, q)) for name, q in zip(("median", "p90", "p95"), qs)},
        **{f"{name}_ci": [float(ci[i, 0]), float(ci[i, 1])] for i, name in enumerate(("median", "p90", "p95"))},
        "n_boot": n_boot_overall, "seed": seed,
    }
    mci = []
    for month, grp in lt.groupby("purchase_month", sort=True):
        c = bootstrap_quantiles(grp.lead_time_days.to_numpy(), (0.5, 0.95), n_boot_month, rng)
        mci.append((month, c[0, 0], c[0, 1], c[1, 0], c[1, 1]))
    ci_df = pd.DataFrame(mci, columns=["purchase_month", "lead_median_lo", "lead_median_hi", "lead_p95_lo", "lead_p95_hi"])
    m = m.merge(ci_df, on="purchase_month", how="left")
    res["monthly"] = m

    # overall base rates ----------------------------------------------------------------------------
    k_late, n_del = int(m.n_late.sum()), int(m.n_delivered.sum())
    k_sev = int(m.n_severe.sum())
    res["overall"] = {
        "n_delivered": n_del, "n_late": k_late, "late_rate": k_late / n_del, "late_ci": wilson(k_late, n_del),
        "n_severe": k_sev, "severe_rate": k_sev / n_del, "severe_ci": wilson(k_sev, n_del),
        "n_late_exact": int(m.n_late_exact.sum()),
        "severe_share_of_late": k_sev / k_late,
        "late_rate_min_month": float(m.late_rate.min()), "late_rate_max_month": float(m.late_rate.max()),
    }

    # (3) promise error and earliness ---------------------------------------------------------------
    res["promise_hist"] = run_sql(con, "promise_error_hist")
    res["promise_summary"] = run_sql(con, "promise_summary").iloc[0].to_dict()
    # (4) severity -------------------------------------------------------------------------------------
    sev = run_sql(con, "severity")
    sev["share_of_delivered"] = sev.n_orders / sev.n_orders.sum()
    res["severity_bands"] = sev
    res["severity_among_late"] = run_sql(con, "severity_among_late").iloc[0].to_dict()

    # (5) all-order fulfilment outcomes (separate KPI family) -----------------------------------------
    f = run_sql(con, "monthly_fulfilment")
    for c in CLASSES:
        f[c + "_share"] = f[c] / f.n_orders
    res["fulfilment_monthly"] = f
    tot = f[["n_orders"] + CLASSES].sum()
    res["fulfilment_total"] = {c: int(tot[c]) for c in ["n_orders"] + CLASSES}
    res["fulfilment_shares_ci"] = {c: [tot[c] / tot.n_orders, *wilson(tot[c], tot.n_orders)] for c in CLASSES}
    res["status_by_class"] = run_sql(con, "status_by_class")
    # Illustrative scenario bounds for the unresolved (open past promise) orders. NOT estimates.
    d, late_n, op = int(tot.delivered_on_time + tot.delivered_late), int(tot.delivered_late), int(tot.open_past_promise)
    res["scenario_bounds"] = {
        "delivered": d, "late": late_n, "open_past_promise": op,
        "late_rate_if_no_open_order_late": late_n / (d + op),
        "late_rate_observed_delivered_only": late_n / d,
        "late_rate_if_all_open_orders_late": (late_n + op) / (d + op),
    }
    f2 = f.copy()
    dm = f2.delivered_on_time + f2.delivered_late
    f2["scenario_lo"] = f2.delivered_late / (dm + f2.open_past_promise)
    f2["observed"] = f2.delivered_late / dm
    f2["scenario_hi"] = (f2.delivered_late + f2.open_past_promise) / (dm + f2.open_past_promise)
    res["scenario_monthly"] = f2[["purchase_month", "scenario_lo", "observed", "scenario_hi"]]

    # (6) high-volume months and promised-lead groups ---------------------------------------------------
    pb = run_sql(con, "promise_buckets")
    pb = add_wilson(pb, "n_late", "n_delivered", "late_rate")
    pb = add_wilson(pb, "n_severe", "n_delivered", "severe_rate")
    res["promise_groups"] = pb
    lo, hi = pb.iloc[0], pb.iloc[-1]
    res["promise_group_diff_shortest_vs_longest"] = newcombe_diff(int(lo.n_late), int(lo.n_delivered), int(hi.n_late), int(hi.n_delivered))

    mb = run_sql(con, "month_by_promise_group")
    pooled = mb.groupby("promised_group")[["n_late", "n_delivered"]].sum()
    pooled["rate"] = pooled.n_late / pooled.n_delivered
    mb = mb.merge(pooled[["rate"]], left_on="promised_group", right_index=True)
    mb["expected_late"] = mb.n_delivered * mb.rate
    exp = mb.groupby("purchase_month")[["n_late", "expected_late", "n_delivered"]].sum().reset_index()
    exp["oe_ratio"] = exp.n_late / exp.expected_late
    exp["expected_rate"] = exp.expected_late / exp.n_delivered
    m = m.merge(exp[["purchase_month", "expected_late", "oe_ratio", "expected_rate"]], on="purchase_month")
    res["monthly"] = m
    mix = mb.pivot(index="purchase_month", columns="promised_group", values="n_delivered")
    res["promise_mix_by_month"] = (mix.div(mix.sum(axis=1), axis=0)).reset_index()

    hv = m[m.high_volume]
    ot = m[~m.high_volume]
    kh, nh, ko, no = int(hv.n_late.sum()), int(hv.n_delivered.sum()), int(ot.n_late.sum()), int(ot.n_delivered.sum())
    d_, dl, du = newcombe_diff(kh, nh, ko, no)
    res["high_volume"] = {
        "rule": f"top {n_high} of {len(m)} cohort months by delivered-order count",
        "months": [str(x) for x in hv.purchase_month], "n_high": nh, "late_high": kh, "rate_high": kh / nh,
        "rate_high_ci": wilson(kh, nh), "n_other": no, "late_other": ko, "rate_other": ko / no,
        "rate_other_ci": wilson(ko, no), "diff": d_, "diff_ci": [dl, du],
        "oe_high": float(hv.n_late.sum() / hv.expected_late.sum()), "oe_other": float(ot.n_late.sum() / ot.expected_late.sum()),
        "spearman_volume_vs_rate_all": float(m.n_delivered.corr(m.late_rate, method="spearman")),
        "spearman_volume_vs_oe_all": float(m.n_delivered.corr(m.oe_ratio, method="spearman")),
        "n_months": int(len(m)),
    }
    # named peak month versus its neighbours (pre-specified hypothesis H1.1)
    pk = m[m.purchase_month == dt.date(2017, 11, 1)].iloc[0]
    nb = m[m.purchase_month.isin([dt.date(2017, 10, 1), dt.date(2017, 12, 1)])]
    pd_, pl, pu = newcombe_diff(int(pk.n_late), int(pk.n_delivered), int(nb.n_late.sum()), int(nb.n_delivered.sum()))
    res["peak_vs_neighbours"] = {"peak_rate": float(pk.late_rate), "peak_n": int(pk.n_delivered),
                                 "neighbour_rate": float(nb.n_late.sum() / nb.n_delivered.sum()),
                                 "neighbour_n": int(nb.n_delivered.sum()), "diff": pd_, "diff_ci": [pl, pu],
                                 "peak_oe": float(pk.oe_ratio)}

    # sensitivity checks ----------------------------------------------------------------------------------
    rows = []
    for label, override in VARIANTS.items():
        params = {**BASE, **override}
        h = run_sql(con, "headline", params).iloc[0].to_dict()
        fc = run_sql(con, "headline_fulfilment", params).set_index("fulfilment_class").n_orders.to_dict()
        n, k, s = int(h["n_delivered"]), int(h["n_late"]), int(h["n_severe"])
        lo_, hi_ = wilson(k, n)
        n_orders = sum(fc.values())
        rows.append({"variant": label, "n_delivered": n, "n_late": k, "late_rate": k / n, "late_lo": lo_, "late_hi": hi_,
                     "severe_rate": s / n, "late_exact_rate": int(h["n_late_exact"]) / n, "lead_median": h["lead_median"],
                     "lead_p90": h["lead_p90"], "lead_p95": h["lead_p95"], "first_cohort": str(h["first_cohort"])[:7],
                     "last_cohort": str(h["last_cohort"])[:7], "n_orders_all": n_orders,
                     "open_past_promise_share": fc.get("open_past_promise", 0) / n_orders,
                     "cancelled_unavailable_share": fc.get("cancelled_unavailable", 0) / n_orders})
    sens = pd.DataFrame(rows)
    sens["late_rate_change_pp"] = (sens.late_rate - sens.late_rate.iloc[0]) * 100
    res["sensitivity"] = sens
    res["n_anomaly_orders_in_base"] = int(con.execute(
        "SELECT count(*) FROM fact_orders WHERE is_delivery_kpi_eligible AND ts_sequence_violation").fetchone()[0])
    return res


# ------------------------------------------------------------------ outputs
def _jsonable(o):
    if isinstance(o, (dt.date, dt.datetime, pd.Timestamp)):
        return str(o)[:10]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def write_outputs(res: dict) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    scalars = {}
    for key, val in res.items():
        if isinstance(val, pd.DataFrame):
            val.to_csv(TABLE_DIR / f"delivery_{key}.csv", index=False)
        else:
            scalars[key] = val
    STATS_JSON.write_text(json.dumps(scalars, indent=2, default=_jsonable), encoding="utf-8")


# ------------------------------------------------------------------ charts
INK, INK2, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3de"
BLUE, ORANGE, AQUA, GREY = "#2a78d6", "#eb6834", "#1baf7a", "#8d8c86"


def _style():
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 9.5,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "legend.frameon": False, "lines.linewidth": 2.0, "lines.markersize": 5,
    })


def make_figures(res: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    _style()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    m = res["monthly"].copy()
    m["x"] = pd.to_datetime(m.purchase_month)
    paths = []

    def save(fig, name):
        p = FIG_DIR / f"delivery_{name}.png"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)

    # F1: late and severe rates per cohort + volume panel
    fig, (ax, axn) = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True, gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})
    ax.fill_between(m.x, m.late_rate_lo, m.late_rate_hi, color=BLUE, alpha=0.18, linewidth=0)
    ax.plot(m.x, m.late_rate, color=BLUE, marker="o", label="Late (calendar date)")
    ax.fill_between(m.x, m.severe_rate_lo, m.severe_rate_hi, color=ORANGE, alpha=0.18, linewidth=0)
    ax.plot(m.x, m.severe_rate, color=ORANGE, marker="o", label="Severe (> 7 days late)")
    imm = m[~m.mature_60d]
    if len(imm):
        ax.axvspan(imm.x.min() - pd.Timedelta(days=15), imm.x.max() + pd.Timedelta(days=15), color=GREY, alpha=0.12, linewidth=0)
        ax.text(imm.x.min(), ax.get_ylim()[1] * 0.97, "60-day maturity:\nexcluded in\nsensitivity", fontsize=8, color=INK2, va="top", ha="center")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=None))
    ax.set_ylabel("Share of delivered orders")
    ax.set_title("Late-delivery rate by purchase month, delivered orders only (95% Wilson bands)")
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.88))
    axn.bar(m.x, m.n_delivered, width=22, color=BLUE, alpha=0.55)
    axn.set_ylabel("Delivered\norders")
    axn.grid(axis="x", visible=False)
    axn.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    axn.set_xticks(m.x)
    plt.setp(axn.get_xticklabels(), rotation=60, ha="right")
    save(fig, "01_monthly_late_rate")

    # F2: lead-time quantiles per cohort
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for col, color, label in (("lead_p95", AQUA, "p95"), ("lead_p90", ORANGE, "p90"), ("lead_median", BLUE, "median")):
        ax.plot(m.x, m[col], color=color, marker="o")
        ax.text(m.x.iloc[-1] + pd.Timedelta(days=12), m[col].iloc[-1], f"{label} {m[col].iloc[-1]:.1f}d", color=INK2, va="center", fontsize=8.5)
    ax.fill_between(m.x, m.lead_median_lo, m.lead_median_hi, color=BLUE, alpha=0.18, linewidth=0)
    ax.set_ylabel("Purchase-to-delivery lead time (days)")
    ax.set_title("Actual delivery lead time by purchase month (median band = 95% bootstrap)")
    ax.set_xlim(m.x.min() - pd.Timedelta(days=15), m.x.max() + pd.Timedelta(days=90))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    ax.set_xticks(m.x[::2])
    plt.setp(ax.get_xticklabels(), rotation=60, ha="right")
    save(fig, "02_lead_time_by_month")

    # F3: promise-error histogram
    h = res["promise_hist"].copy()
    total = h.n_orders.sum()
    lo, hi = -40, 30
    view = h[(h.promise_error_days >= lo) & (h.promise_error_days <= hi)]
    out_left, out_right = int(h[h.promise_error_days < lo].n_orders.sum()), int(h[h.promise_error_days > hi].n_orders.sum())
    fig, ax = plt.subplots(figsize=(9, 4.2))
    colors = [BLUE if v <= 0 else ORANGE for v in view.promise_error_days]
    ax.bar(view.promise_error_days, view.n_orders / total, width=0.85, color=colors)
    ax.axvline(7.5, color=INK2, linestyle="--", linewidth=1)
    ax.text(8, ax.get_ylim()[1] * 0.92, "severe: > 7 days late", color=INK2, fontsize=8.5)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=None))
    ax.set_xlabel("Promise error in calendar days (negative = delivered before the promised date)")
    ax.set_ylabel("Share of delivered orders")
    ax.set_title(f"Promise-error distribution (blue = on time, orange = late; {out_left:,} orders < {lo}, {out_right:,} > {hi} not shown)")
    save(fig, "03_promise_error_distribution")

    # F4: all-order fulfilment outcomes (shares of ALL window orders)
    f = res["fulfilment_monthly"].copy()
    f["x"] = pd.to_datetime(f.purchase_month)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for col, color, label in (("delivered_late_share", BLUE, "Delivered late"), ("open_past_promise_share", ORANGE, "Open, past promise (unresolved)"),
                              ("cancelled_unavailable_share", AQUA, "Cancelled / unavailable")):
        ax.plot(f.x, f[col], color=color, marker="o", label=label)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=None))
    ax.set_ylabel("Share of ALL orders purchased that month")
    ax.set_title("All-order fulfilment outcomes by purchase month (separate from delivered-only KPIs)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    ax.set_xticks(f.x[::2])
    plt.setp(ax.get_xticklabels(), rotation=60, ha="right")
    save(fig, "04_fulfilment_outcomes")

    # F5: promised-lead groups
    pb = res["promise_groups"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    xs = np.arange(len(pb))
    for off, col, color, label in ((-0.08, "late_rate", BLUE, "Late"), (0.08, "severe_rate", ORANGE, "Severe (> 7 days)")):
        y = pb[col].to_numpy()
        ax.errorbar(xs + off, y, yerr=[y - pb[col + "_lo"].to_numpy(), pb[col + "_hi"].to_numpy() - y], fmt="o", color=color,
                    capsize=3, label=label, linewidth=1.6)
        for xi, yi in zip(xs + off, y):
            ax.text(xi + 0.12, yi, f"{yi:.1%}", fontsize=8, color=INK2, va="center")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{g.split(': ')[1]}\n(n={n:,})" for g, n in zip(pb.promised_group, pb.n_delivered)])
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=None))
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Share of delivered orders")
    ax.set_xlabel("Promised lead time (purchase date to estimated date)")
    ax.set_title("Observed late rate by promised lead time (95% Wilson)")
    ax.legend(loc="upper right")
    save(fig, "05_promised_lead_groups")

    # F6: observed vs promise-mix-expected late rate; high-volume months shaded
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for x in m.x[m.high_volume]:
        ax.axvspan(x - pd.Timedelta(days=15), x + pd.Timedelta(days=15), color=GREY, alpha=0.15, linewidth=0)
    ax.plot(m.x, m.late_rate, color=BLUE, marker="o", label="Observed late rate")
    ax.plot(m.x, m.expected_rate, color=GREY, marker="s", linestyle="--", linewidth=1.5, label="Expected given promised-lead mix")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=None))
    ax.set_ylabel("Share of delivered orders")
    ax.set_title("Observed vs expected late rate by month (grey bands = high-volume months)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    ax.set_xticks(m.x[::2])
    plt.setp(ax.get_xticklabels(), rotation=60, ha="right")
    save(fig, "06_observed_vs_expected")

    # F7: sensitivity forest
    s = res["sensitivity"].iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.errorbar(s.late_rate, s.index, xerr=[s.late_rate - s.late_lo, s.late_hi - s.late_rate], fmt="o", color=BLUE, capsize=3)
    for i, r in s.iterrows():
        ax.text(s.late_hi.max() + 0.0012, i, f"{r.late_rate:.2%}  (n={int(r.n_delivered):,})", va="center", fontsize=8.5, color=INK2)
    ax.axvline(res["sensitivity"].late_rate.iloc[0], color=GREY, linestyle="--", linewidth=1)
    ax.set_yticks(s.index)
    ax.set_yticklabels(s.variant)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    ax.set_xlim(s.late_lo.min() - 0.001, s.late_hi.max() + 0.0075)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Late-delivery rate, delivered orders (95% Wilson)")
    ax.set_title("Sensitivity of the headline late rate")
    save(fig, "07_sensitivity")
    return paths


def main() -> int:
    if not DB.exists():
        print("Model not found. Run: python scripts/build_model.py", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    try:
        res = compute_all(con)
    finally:
        con.close()
    write_outputs(res)
    for p in make_figures(res):
        print("wrote", p.relative_to(ROOT))
    print("wrote", STATS_JSON.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
