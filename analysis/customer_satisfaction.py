"""Workstream 3 - Customer satisfaction: delivery lateness and review scores (association only).

Usage (project root, after `python scripts/build_model.py`):
    python analysis/customer_satisfaction.py

Primary aggregations are SQL (sql/analysis/satisfaction/*.sql) on the validated DuckDB model; Python adds
intervals, cluster bootstrap (resampling customers), non-response bounds, adjusted regression and charts.
Primary population: `is_review_kpi_eligible` = delivered in window with exactly one review row.
Nothing here is causal: all results are observational associations.
Outputs: reports/tables/satisfaction_*.csv, reports/satisfaction_stats.json, reports/figures/satisfaction_*.png.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_reliability as dr  # noqa: E402  (Wilson / Newcombe helpers and chart style)

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql" / "analysis" / "satisfaction"
DB = ROOT / "data" / "processed" / "olist_model.duckdb"
TABLE_DIR = ROOT / "reports" / "tables"
FIG_DIR = ROOT / "reports" / "figures"
STATS_JSON = ROOT / "reports" / "satisfaction_stats.json"

SEED = 20241001
MIN_STATE_N = 100        # blueprint R8: states below 100 delivered orders are pooled
MIN_CATEGORY_N = 500     # pre-specified: categories below 500 reviewed orders are pooled
EPISODE_MONTHS = ["2017-11", "2018-02", "2018-03"]   # the three late-rate episodes from the delivery workstream
BAND_ORDER = ["<=0", "1-3", "4-7", "8+"]
PROMISE_LABEL = {"1: <=14 days": "p1_le14", "2: 15-21 days": "p2_15_21", "3: 22-28 days": "p3_22_28",
                 "4: 29-35 days": "p4_29_35", "5: 36+ days": "p5_36plus"}
BAND_LABEL = {"<=0": "on_time", "1-3": "late_1_3", "4-7": "late_4_7", "8+": "late_8plus"}


# ------------------------------------------------------------------ SQL helpers
def run_sql(con, name: str, params: dict | None = None) -> pd.DataFrame:
    sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    df = con.execute(sql, params).df() if params else con.execute(sql).df()
    if "purchase_month" in df.columns:
        df["purchase_month"] = pd.to_datetime(df["purchase_month"]).dt.date
    return df


def group_stats(df: pd.DataFrame, label_col: str | None = None) -> pd.DataFrame:
    """Add mean (normal-approximation CI), Wilson low-score share and score-share columns to an SQL summary."""
    df = df.copy()
    df["mean_score"] = df.sum_score / df.n
    se = df.sd_score / np.sqrt(df.n)
    df["mean_lo"], df["mean_hi"] = df.mean_score - dr.Z95 * se, df.mean_score + dr.Z95 * se
    df = dr.add_wilson(df, "n_low", "n", "low_share")
    for k in range(1, 6):
        df[f"share{k}"] = df[f"n{k}"] / df.n
    return df


# ------------------------------------------------------------------ cluster bootstrap effect sizes
# Per-customer count matrix columns:
#  0 n_on, 1 n_late, 2 sum_on, 3 sum_late, 4 sumsq_on, 5 sumsq_late, 6 low_on, 7 low_late, 8-12 on counts of 1..5, 13-17 late counts of 1..5
def customer_matrix(cust: np.ndarray, late: np.ndarray, score: np.ndarray, integer_scores: bool = True) -> np.ndarray:
    n_cust = int(cust.max()) + 1
    M = np.zeros((n_cust, 18))
    low = (score <= 2).astype(float)
    for g, off in ((0, 0), (1, 1)):
        m = late == g
        c = cust[m]
        np.add.at(M[:, 0 + off], c, 1.0)
        np.add.at(M[:, 2 + off], c, score[m])
        np.add.at(M[:, 4 + off], c, score[m] ** 2)
        np.add.at(M[:, 6 + off], c, low[m])
        if integer_scores:
            for k in range(1, 6):
                mk = m & (score == k)
                np.add.at(M[:, 8 + 5 * g + (k - 1)], cust[mk], 1.0)
    return M


def effects_from_totals(T: np.ndarray, with_cliff: bool = True) -> dict[str, np.ndarray]:
    """Effect sizes (late versus on time) from totals T of shape (..., 18)."""
    n_on, n_late, s_on, s_late, ss_on, ss_late, low_on, low_late = (T[..., i] for i in range(8))
    mean_on, mean_late = s_on / n_on, s_late / n_late
    var_on = (ss_on - n_on * mean_on ** 2) / (n_on - 1)
    var_late = (ss_late - n_late * mean_late ** 2) / (n_late - 1)
    pooled = np.sqrt(((n_on - 1) * var_on + (n_late - 1) * var_late) / (n_on + n_late - 2))
    p_on, p_late = low_on / n_on, low_late / n_late
    out = {
        "mean_on": mean_on, "mean_late": mean_late, "mean_diff": mean_late - mean_on,
        "cohens_d": (mean_late - mean_on) / pooled,
        "low_on": p_on, "low_late": p_late, "low_diff": p_late - p_on, "risk_ratio": p_late / p_on,
        "odds_ratio": (p_late / (1 - p_late)) / (p_on / (1 - p_on)),
    }
    if with_cliff:
        a, b = T[..., 8:13], T[..., 13:18]
        cum_b = np.cumsum(b, axis=-1)
        below = cum_b - b                       # late counts strictly below each score
        above = b.sum(axis=-1, keepdims=True) - cum_b
        denom = n_on * n_late
        p_gt, p_lt, p_tie = (a * below).sum(-1) / denom, (a * above).sum(-1) / denom, (a * b).sum(-1) / denom
        out["cliffs_delta"] = p_gt - p_lt                       # > 0: on-time order tends to score higher
        out["prob_superiority"] = p_gt + 0.5 * p_tie            # P(on-time score > late score), ties split
    return out


def cluster_bootstrap(cust: np.ndarray, late: np.ndarray, score: np.ndarray, n_boot: int, rng: np.random.Generator,
                      integer_scores: bool = True) -> pd.DataFrame:
    """Point estimates and 95% percentile intervals, resampling customers (clusters) with replacement."""
    M = customer_matrix(cust, late, score, integer_scores)
    n_cust = M.shape[0]
    point = effects_from_totals(M.sum(axis=0), integer_scores)
    reps = np.empty((n_boot, 18))
    for b in range(n_boot):
        w = np.bincount(rng.integers(0, n_cust, n_cust), minlength=n_cust)
        reps[b] = w @ M
    boot = effects_from_totals(reps, integer_scores)
    rows = []
    for k, v in point.items():
        lo, hi = np.percentile(boot[k], [2.5, 97.5])
        rows.append({"metric": k, "estimate": float(v), "ci_lo": float(lo), "ci_hi": float(hi), "boot_se": float(np.std(boot[k], ddof=1))})
    return pd.DataFrame(rows)


def effect_row(label: str, boot: pd.DataFrame, n_on: int, n_late: int, units: str = "orders") -> dict:
    g = boot.set_index("metric")
    row = {"variant": label, "units": units, "n_on_time": n_on, "n_late": n_late}
    for k in g.index:
        row[k] = g.loc[k, "estimate"]
        row[k + "_lo"] = g.loc[k, "ci_lo"]
        row[k + "_hi"] = g.loc[k, "ci_hi"]
    iid_se = np.sqrt(row["low_on"] * (1 - row["low_on"]) / n_on + row["low_late"] * (1 - row["low_late"]) / n_late)
    row["design_effect_ratio_low_diff"] = g.loc["low_diff", "boot_se"] / iid_se   # cluster SE / independent-orders SE
    return row


# ------------------------------------------------------------------ regression
def prepare_design(p0: pd.DataFrame) -> pd.DataFrame:
    """Analysis frame for regression. All collapsing rules are fixed in advance (see module constants)."""
    d = p0.copy()
    d["low"] = (d.review_score <= 2).astype(int)
    d["late"] = d.is_late_calendar.astype(int)
    d["month"] = pd.to_datetime(d.purchase_month).dt.strftime("%Y-%m")
    n_state = d.customer_state.value_counts()
    d["state_g"] = np.where(d.customer_state.isin(n_state[n_state >= MIN_STATE_N].index), d.customer_state, "OtherLowVolume")
    n_cat = d.order_category.value_counts()
    d["category_g"] = np.where(d.order_category.isin(n_cat[n_cat >= MIN_CATEGORY_N].index), d.order_category, "other_category")
    d["promised_g"] = d.promised_group.map(PROMISE_LABEL)
    d["log_value"] = np.log(d.items_value)
    d["freight_share"] = d.freight_value / (d.items_value + d.freight_value)
    d["items_g"] = np.select([d.n_items == 1, d.n_items == 2], ["i1", "i2"], "i3plus")
    d["band"] = d.days_late_band.map(BAND_LABEL)
    d["cust"] = pd.factorize(d.customer_unique_id)[0]
    return d


COVARIATES_M1 = "C(promised_g, Treatment('p3_22_28')) + C(month)"
COVARIATES_M2 = COVARIATES_M1 + " + C(state_g, Treatment('SP')) + log_value + freight_share + C(items_g, Treatment('i1'))"


def _fit(formula: str, d: pd.DataFrame, family: str = "logit", cluster: bool = True):
    import statsmodels.formula.api as smf
    model = smf.logit(formula, d) if family == "logit" else smf.ols(formula, d)
    kw = dict(cov_type="cluster", cov_kwds={"groups": d["cust"].to_numpy()}) if cluster else {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = model.fit(disp=0, maxiter=200, **kw) if family == "logit" else model.fit(**kw)
    return res, [str(w.message) for w in caught]


def _or_row(label: str, res, term: str, d: pd.DataFrame, warns: list[str], spec: str) -> dict:
    b = res.params[term]
    lo, hi = res.conf_int().loc[term]
    return {"model": label, "spec": spec, "n": int(res.nobs), "events": int(d.low.sum()), "odds_ratio": float(np.exp(b)),
            "or_lo": float(np.exp(lo)), "or_hi": float(np.exp(hi)), "converged": bool(res.mle_retvals.get("converged", True)),
            "warnings": "; ".join(sorted(set(warns)))[:200]}


def regression_suite(p0: pd.DataFrame, leave_one_out: bool = True) -> dict:
    d = prepare_design(p0)
    out: dict = {}
    rows, extra = [], {}
    specs = {
        "M0 unadjusted": "low ~ late",
        "M1 + promised-lead group + purchase month": f"low ~ late + {COVARIATES_M1}",
        "M2 + state, order value, freight share, item count (primary)": f"low ~ late + {COVARIATES_M2}",
        "M3 = M2 + product category (exploratory)": f"low ~ late + {COVARIATES_M2} + C(category_g, Treatment('bed_bath_table'))",
    }
    fits = {}
    for label, f in specs.items():
        res, w = _fit(f, d)
        fits[label] = res
        rows.append(_or_row(label, res, "late", d, w, f))
    # naive (non-clustered) SE for the primary model, to show the effect of clustering
    prim_label = "M2 + state, order value, freight share, item count (primary)"
    naive, w = _fit(specs[prim_label], d, cluster=False)
    prim = fits[prim_label]
    extra["se_cluster_vs_naive_late"] = [float(prim.bse["late"]), float(naive.bse["late"])]
    # average marginal effect of lateness on P(low score) from the primary model
    me = prim.get_margeff(at="overall", method="dydx", dummy=True).summary_frame()
    me_late = me.loc["late"]
    lo_col = [c for c in me.columns if "Low" in c][0]
    hi_col = [c for c in me.columns if "Hi" in c][0]
    extra["ame_late_pp"] = [float(me_late["dy/dx"]) * 100, float(me_late[lo_col]) * 100, float(me_late[hi_col]) * 100]
    # lateness bands instead of the binary indicator
    band_f = "low ~ C(band, Treatment('on_time')) + " + COVARIATES_M2
    res_b, wb = _fit(band_f, d)
    band_rows = []
    for k in ("late_1_3", "late_4_7", "late_8plus"):
        term = f"C(band, Treatment('on_time'))[T.{k}]"
        band_rows.append({"band": k, "odds_ratio": float(np.exp(res_b.params[term])),
                          "or_lo": float(np.exp(res_b.conf_int().loc[term, 0])), "or_hi": float(np.exp(res_b.conf_int().loc[term, 1]))})
    # linear model for the score itself (secondary)
    res_ols, _ = _fit(specs[prim_label].replace("low ~", "review_score ~"), d, family="ols")
    lo_o, hi_o = res_ols.conf_int().loc["late"]
    extra["ols_score_diff"] = [float(res_ols.params["late"]), float(lo_o), float(hi_o)]
    # --------------------------------------------------------------- diagnostics
    exog = pd.DataFrame(prim.model.exog, columns=prim.model.exog_names)
    nonconst = exog.drop(columns="Intercept")
    corr = np.corrcoef(nonconst.to_numpy(), rowvar=False)
    vif = pd.Series(np.diag(np.linalg.inv(corr)), index=nonconst.columns)
    z = (nonconst - nonconst.mean()) / nonconst.std(ddof=0)
    extra["collinearity"] = {"vif_late": float(vif["late"]), "max_vif": float(vif.max()), "max_vif_term": str(vif.idxmax()),
                             "n_terms_vif_gt_5": int((vif > 5).sum()), "n_terms": int(len(vif)),
                             "terms_vif_gt_5": [str(t) for t in vif[vif > 5].index],
                             "condition_number_standardised": float(np.linalg.cond(z.to_numpy()))}
    extra["missingness"] = {c: int(p0[c].isna().sum()) for c in ["review_score", "customer_state", "promised_lead_days", "items_value", "freight_value", "n_items", "order_category", "purchase_month"]}
    sparse = []
    for col, label in (("state_g", "customer state"), ("category_g", "product category"), ("promised_g", "promised-lead group"),
                       ("items_g", "item count"), ("month", "purchase month")):
        t = d.groupby(col).agg(n=("low", "size"), events=("low", "sum"))
        sparse.append({"factor": label, "levels": int(len(t)), "min_n": int(t.n.min()), "min_events": int(t.events.min()),
                       "levels_with_lt_50_events": int((t.events < 50).sum())})
    cell = d.groupby(["month", "late"]).agg(n=("low", "size"), events=("low", "sum"))
    sparse.append({"factor": "purchase month x late", "levels": int(len(cell)), "min_n": int(cell.n.min()), "min_events": int(cell.events.min()),
                   "levels_with_lt_50_events": int((cell.events < 50).sum())})
    out["sparsity"] = pd.DataFrame(sparse)
    out["category_levels"] = d.groupby("category_g").agg(n=("low", "size"), events=("low", "sum")).reset_index().sort_values("n", ascending=False)
    out["state_levels"] = d.groupby("state_g").agg(n=("low", "size"), events=("low", "sum")).reset_index().sort_values("n", ascending=False)
    # --------------------------------------------------------------- stability of the late association (M2 specification)
    stab = []

    def stability(label, sub, formula=specs[prim_label]):
        res, w = _fit(formula, sub)
        stab.append(_or_row(label, res, "late", sub, w, "M2 specification"))
        return stab[-1]

    stability("Primary population (M2)", d)
    stab[-1]["events"] = int(d.low.sum())
    stability("P1: reviews created before delivery removed", d[~d.review_before_delivery_flag.astype(bool)])
    stability("Excluding the 3 late-rate episode months", d[~d.month.isin(EPISODE_MONTHS)])
    stability("Only the 3 late-rate episode months", d[d.month.isin(EPISODE_MONTHS)],
              specs[prim_label].replace(" + C(month)", ""))
    first = d.month <= "2017-10"
    stability("First half (purchases 2017-01..2017-10)", d[first])
    stability("Second half (purchases 2017-11..2018-08)", d[~first])
    loo = []
    if leave_one_out:
        for m in sorted(d.month.unique()):
            sub = d[d.month != m]
            res, _ = _fit(specs[prim_label], sub)
            loo.append({"left_out_month": m, "odds_ratio": float(np.exp(res.params["late"])),
                        "or_lo": float(np.exp(res.conf_int().loc["late", 0])), "or_hi": float(np.exp(res.conf_int().loc["late", 1]))})
    out.update(models=pd.DataFrame(rows), bands_adj=pd.DataFrame(band_rows), stability=pd.DataFrame(stab),
               leave_one_month_out=pd.DataFrame(loo), regression_extra=extra)
    return out


# ------------------------------------------------------------------ main computation
def compute_all(con: duckdb.DuckDBPyConnection, n_boot: int = 1000, seed: int = SEED, leave_one_out: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    res: dict = {"seed": seed, "n_boot": n_boot}
    base = run_sql(con, "analysis_base")
    base["customer_code"] = pd.factorize(base.customer_unique_id)[0]
    p0 = base[base.review_row_count == 1].copy()
    res["populations"] = {
        "delivered_window": int(len(base)), "one_review_p0": int(len(p0)),
        "no_review": int((base.review_row_count == 0).sum()), "multi_review": int((base.review_row_count > 1).sum()),
        "p1_after_removing_early": int((~p0.review_before_delivery_flag.astype(bool)).sum()),
        "early_reviews": int(p0.review_before_delivery_flag.astype(bool).sum()),
        "p1b_after_removing_same_day": int((p0.review_creation_date > p0.delivered_date).sum()),
        "n_customers_p0": int(p0.customer_code.nunique()),
    }

    # (1)(2) score distribution and on-time vs late, with primary + timing sensitivities --------------
    variants = {"P0 primary: one review row": dict(excl_early=False, excl_same_day=False),
                "P1: reviews created before delivery removed": dict(excl_early=True, excl_same_day=False),
                "P1b: reviews created on or before delivery day removed": dict(excl_early=True, excl_same_day=True)}
    masks = {"P0 primary: one review row": np.ones(len(p0), bool),
             "P1: reviews created before delivery removed": ~p0.review_before_delivery_flag.astype(bool).to_numpy(),
             "P1b: reviews created on or before delivery day removed": (p0.review_creation_date > p0.delivered_date).to_numpy()}
    gs_rows, effect_rows = [], []
    for label, prm in variants.items():
        g = group_stats(run_sql(con, "group_summary", prm))
        g.insert(0, "variant", label)
        gs_rows.append(g)
        m = masks[label]
        boot = cluster_bootstrap(p0.customer_code.to_numpy()[m], p0.is_late_calendar.to_numpy()[m].astype(int),
                                 p0.review_score.to_numpy()[m].astype(float), n_boot, rng)
        n_on = int(g.loc[g.is_late == False, "n"].iloc[0]); n_late = int(g.loc[g.is_late == True, "n"].iloc[0])  # noqa: E712
        effect_rows.append(effect_row(label, boot, n_on, n_late))
    gs = pd.concat(gs_rows, ignore_index=True)
    res["group_summary"] = gs
    res["overall_distribution"] = pd.DataFrame({
        "score": [1, 2, 3, 4, 5],
        "n": [int(gs[gs.variant.str.startswith("P0")][f"n{k}"].sum()) for k in range(1, 6)]})
    res["overall_distribution"]["share"] = res["overall_distribution"].n / res["overall_distribution"].n.sum()
    tot_low = int(gs[gs.variant.str.startswith("P0")].n_low.sum())
    tot_n = int(gs[gs.variant.str.startswith("P0")].n.sum())
    res["overall"] = {"n": tot_n, "mean_score": float(res["overall_distribution"].eval("score*n").sum() / tot_n), "n_low": tot_low,
                      "low_share": tot_low / tot_n, "low_ci": dr.wilson(tot_low, tot_n)}
    p0_g = gs[gs.variant.str.startswith("P0")].set_index("is_late")
    d_, dl, du = dr.newcombe_diff(int(p0_g.loc[True, "n_low"]), int(p0_g.loc[True, "n"]), int(p0_g.loc[False, "n_low"]), int(p0_g.loc[False, "n"]))
    res["low_diff_newcombe_iid"] = [d_, dl, du]

    # (3) severity bands (primary and P1) -------------------------------------------------------------
    bands = []
    for label, prm in list(variants.items())[:2]:
        b = group_stats(run_sql(con, "band_summary", prm))
        b["band_order"] = b.days_late_band.map({k: i for i, k in enumerate(BAND_ORDER)})
        b = b.sort_values("band_order").drop(columns="band_order")
        ref = b[b.days_late_band == "<=0"].iloc[0]
        diffs = [dr.newcombe_diff(int(r.n_low), int(r.n), int(ref.n_low), int(ref.n)) for r in b.itertuples()]
        b["low_diff_vs_on_time"] = [x[0] for x in diffs]
        b["low_diff_lo"] = [x[1] for x in diffs]
        b["low_diff_hi"] = [x[2] for x in diffs]
        b["mean_diff_vs_on_time"] = b.mean_score - ref.mean_score
        se = np.sqrt((b.sd_score ** 2 / b.n) + (ref.sd_score ** 2 / ref.n))
        b["mean_diff_lo"], b["mean_diff_hi"] = b.mean_diff_vs_on_time - dr.Z95 * se, b.mean_diff_vs_on_time + dr.Z95 * se
        b.insert(0, "variant", label)
        bands.append(b)
    res["bands"] = pd.concat(bands, ignore_index=True)

    # (5) alternative handling of multi-review orders -------------------------------------------------
    rules = run_sql(con, "multi_review_rules")
    rows_df = run_sql(con, "review_rows")
    rule_cols = {"2 lowest score per order": "min_score", "3 highest score per order": "max_score",
                 "4 mean score per order": "mean_score", "5 earliest-dated review (orders with distinct dates)": "earliest_score",
                 "6 latest-dated review (orders with distinct dates; comparison only)": "latest_score"}
    multi = base[base.review_row_count >= 1]
    for rule, col in rule_cols.items():
        sub = multi[multi[col].notna()]
        boot = cluster_bootstrap(sub.customer_code.to_numpy(), sub.is_late_calendar.to_numpy().astype(int), sub[col].to_numpy().astype(float),
                                 n_boot, rng, integer_scores=(col != "mean_score"))
        r_ = rules[rules.rule == rule].set_index("is_late")
        effect_rows.append(effect_row("Multi-review rule: " + rule[2:], boot, int(r_.loc[False, "n_units"]), int(r_.loc[True, "n_units"])))
    rows_df["cust"] = pd.factorize(rows_df.customer_unique_id)[0]
    boot = cluster_bootstrap(rows_df.cust.to_numpy(), rows_df.is_late.to_numpy().astype(int), rows_df.review_score.to_numpy().astype(float), n_boot, rng)
    r_ = rules[rules.rule.str.startswith("7")].set_index("is_late")
    effect_rows.append(effect_row("Multi-review rule: every review row as a unit", boot, int(r_.loc[False, "n_units"]), int(r_.loc[True, "n_units"]), units="review rows"))
    res["multi_review_rules"] = rules
    res["effects"] = pd.DataFrame(effect_rows)

    # (6) non-response: coverage by lateness, bounds, scenarios -----------------------------------------
    nr = run_sql(con, "nonresponse").set_index("is_late")
    nr_out = []
    for late in (False, True):
        r = nr.loc[late]
        lo, hi = dr.wilson(int(r.n_no_review), int(r.n_total))
        nr_out.append({"group": "late" if late else "on time", "n_total": int(r.n_total), "n_one_review": int(r.n_one_review),
                       "n_no_review": int(r.n_no_review), "n_multi_review": int(r.n_multi_review),
                       "no_review_rate": r.n_no_review / r.n_total, "no_review_lo": lo, "no_review_hi": hi,
                       "multi_review_rate": r.n_multi_review / r.n_total,
                       "low_share_among_one": r.n_low_among_one / r.n_one_review,
                       "mean_among_one": r.sum_score_among_one / r.n_one_review})
    res["nonresponse"] = pd.DataFrame(nr_out)
    on, lt = nr.loc[False], nr.loc[True]
    res["no_review_rate_diff"] = list(dr.newcombe_diff(int(lt.n_no_review), int(lt.n_total), int(on.n_no_review), int(on.n_total)))
    unk_on, unk_lt = int(on.n_no_review + on.n_multi_review), int(lt.n_no_review + lt.n_multi_review)
    lo_on, hi_on = on.n_low_among_one / on.n_total, (on.n_low_among_one + unk_on) / on.n_total
    lo_lt, hi_lt = lt.n_low_among_one / lt.n_total, (lt.n_low_among_one + unk_lt) / lt.n_total
    mean_lo_on, mean_hi_on = (on.sum_score_among_one + unk_on * 1) / on.n_total, (on.sum_score_among_one + unk_on * 5) / on.n_total
    mean_lo_lt, mean_hi_lt = (lt.sum_score_among_one + unk_lt * 1) / lt.n_total, (lt.sum_score_among_one + unk_lt * 5) / lt.n_total
    res["worst_case_bounds"] = {
        "unknown_on_time": unk_on, "unknown_late": unk_lt, "n_on_time": int(on.n_total), "n_late": int(lt.n_total),
        "low_share_on_time": [lo_on, hi_on], "low_share_late": [lo_lt, hi_lt],
        "low_diff_bounds": [lo_lt - hi_on, hi_lt - lo_on],
        "mean_diff_bounds": [mean_lo_lt - mean_hi_on, mean_hi_lt - mean_lo_on],
    }
    grid = []
    obs_on, obs_lt = on.n_low_among_one / on.n_one_review, lt.n_low_among_one / lt.n_one_review
    for lab_l, m_l in (("same as reviewed late orders", obs_lt), ("none low-scored", 0.0), ("all low-scored", 1.0)):
        for lab_o, m_o in (("same as reviewed on-time orders", obs_on), ("none low-scored", 0.0), ("all low-scored", 1.0)):
            s_lt = (lt.n_low_among_one + lt.n_no_review * m_l) / (lt.n_one_review + lt.n_no_review)
            s_on = (on.n_low_among_one + on.n_no_review * m_o) / (on.n_one_review + on.n_no_review)
            grid.append({"assumption_missing_late": lab_l, "assumption_missing_on_time": lab_o, "low_share_late": s_lt,
                         "low_share_on_time": s_on, "low_diff_pp": (s_lt - s_on) * 100})
    res["missing_review_scenarios"] = pd.DataFrame(grid)
    res["scenario_note"] = {"n_missing_late": int(lt.n_no_review), "n_missing_on_time": int(on.n_no_review)}

    # early reviews composition ----------------------------------------------------------------------------
    er = run_sql(con, "early_review_composition")
    res["early_review_composition"] = er
    tl = er[er.is_late].n.sum()
    to = er[~er.is_late].n.sum()
    el = int(er[(er.is_late) & (er.created_before_delivery)].n.iloc[0])
    eo = int(er[(~er.is_late) & (er.created_before_delivery)].n.iloc[0])
    res["early_review_rates"] = {"late": [el, int(tl), el / tl, *dr.wilson(el, tl)], "on_time": [eo, int(to), eo / to, *dr.wilson(eo, to)],
                                 "diff": list(dr.newcombe_diff(el, int(tl), eo, int(to)))}
    ec = er[er.created_before_delivery]
    res["early_review_scores"] = {"mean": float(ec.sum_score.sum() / ec.n.sum()), "low_share": float(ec.n_low.sum() / ec.n.sum()), "n": int(ec.n.sum())}

    # when were the early reviews written relative to the PROMISED date? (descriptive)
    et = run_sql(con, "early_review_timing")
    et["band_order"] = et.days_late_band.map({k: i for i, k in enumerate(BAND_ORDER)})
    et = dr.add_wilson(et.sort_values("band_order").drop(columns="band_order").reset_index(drop=True), "n_early", "n", "early_share")
    res["early_timing"] = et
    late_et = et[et.days_late_band != "<=0"]
    n_e = int(late_et.n_early.sum())
    n_after = int(late_et.n_early_after_promise.sum())
    low_after = int(late_et.low_early_after_promise.sum())
    low_before = int(late_et.low_early_on_or_before_promise.sum())
    res["early_late_orders_timing"] = {
        "n_early_late_orders": n_e, "after_promise": n_after, "on_or_before_promise": int(late_et.n_early_on_or_before_promise.sum()),
        "after_promise_share": n_after / n_e, "after_promise_ci": dr.wilson(n_after, n_e),
        "low_share_after_promise": low_after / n_after, "low_share_on_or_before_promise": low_before / max(1, n_e - n_after)}
    # score by review timing group: on-time / late-and-early / late-and-after-delivery (primary population)
    erc = er.set_index(["created_before_delivery", "is_late"])
    groups = {"On time (review after delivery)": (False, False), "On time (review before delivery)": (True, False),
              "Late, review written before delivery": (True, True), "Late, review written after delivery": (False, True)}
    tg = []
    for lab, key in groups.items():
        r = erc.loc[key]
        lo, hi = dr.wilson(int(r.n_low), int(r.n))
        tg.append({"group": lab, "n": int(r.n), "mean_score": float(r.sum_score / r.n), "low_share": float(r.n_low / r.n), "low_lo": lo, "low_hi": hi})
    res["timing_groups"] = pd.DataFrame(tg)

    # (8) share of low-score reviewed orders that were delivered late (NOT an attributable fraction) -----------
    c8 = []
    for label in variants:
        g = gs[gs.variant == label].set_index("is_late")
        nl_late, nl_on = int(g.loc[True, "n_low"]), int(g.loc[False, "n_low"])
        n_late_r, n_on_r = int(g.loc[True, "n"]), int(g.loc[False, "n"])
        lo, hi = dr.wilson(nl_late, nl_late + nl_on)
        c8.append({"variant": label, "low_score_orders": nl_late + nl_on, "of_which_late": nl_late, "share_late": nl_late / (nl_late + nl_on),
                   "share_lo": lo, "share_hi": hi, "late_share_among_reviewed": n_late_r / (n_late_r + n_on_r),
                   "late_share_among_not_low": (n_late_r - nl_late) / (n_late_r + n_on_r - nl_late - nl_on),
                   "low_orders_on_time": nl_on})
    res["c8"] = pd.DataFrame(c8)
    mn = effects_rule_c8(multi, "min_score")
    res["c8_lowest_rule"] = mn
    b0 = res["bands"][res["bands"].variant.str.startswith("P0")]
    res["low_score_band_composition"] = pd.DataFrame({
        "days_late_band": b0.days_late_band.values, "low_score_orders": b0.n_low.values,
        "share_of_low_score_orders": (b0.n_low / b0.n_low.sum()).values, "share_of_reviewed_orders": (b0.n / b0.n.sum()).values})

    # monthly view ----------------------------------------------------------------------------------------------
    mm = run_sql(con, "monthly_summary")
    mm = dr.add_wilson(mm, "n_low", "n", "low_share")
    mm["mean_score"] = mm.sum_score / mm.n
    res["monthly"] = mm

    # (7) regression --------------------------------------------------------------------------------------------
    reg = regression_suite(p0, leave_one_out=leave_one_out)
    res.update(reg)
    return res


def effects_rule_c8(multi: pd.DataFrame, col: str) -> dict:
    sub = multi[multi[col].notna()]
    low = sub[sub[col] <= 2]
    late = int(low.is_late_calendar.sum())
    lo, hi = dr.wilson(late, len(low))
    return {"low_score_orders": int(len(low)), "of_which_late": late, "share_late": late / len(low), "share_lo": lo, "share_hi": hi}


# ------------------------------------------------------------------ outputs
def write_outputs(res: dict) -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    scalars = {}
    for key, val in res.items():
        if isinstance(val, pd.DataFrame):
            val.to_csv(TABLE_DIR / f"satisfaction_{key}.csv", index=False)
        else:
            scalars[key] = val
    STATS_JSON.write_text(json.dumps(scalars, indent=2, default=dr._jsonable), encoding="utf-8")


# ------------------------------------------------------------------ charts
def make_figures(res: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    dr._style()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    BLUE, ORANGE, AQUA, GREY, INK2 = dr.BLUE, dr.ORANGE, dr.AQUA, dr.GREY, dr.INK2
    paths = []

    def save(fig, name):
        p = FIG_DIR / f"satisfaction_{name}.png"
        fig.savefig(p, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)

    gs = res["group_summary"]
    g0 = gs[gs.variant.str.startswith("P0")].set_index("is_late")

    # F1 score distribution by on-time/late
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    x = np.arange(1, 6)
    for off, late, color, label in ((-0.2, False, BLUE, "On time"), (0.2, True, ORANGE, "Late")):
        y = [g0.loc[late, f"share{k}"] for k in range(1, 6)]
        ax.bar(x + off, y, width=0.38, color=color, label=f"{label} (n={int(g0.loc[late, 'n']):,})")
        for xi, yi in zip(x + off, y):
            ax.text(xi, yi + 0.008, f"{yi:.0%}", ha="center", fontsize=8.5, color=INK2)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Review score (stars)")
    ax.set_ylabel("Share of reviewed orders")
    ax.set_title("Review score distribution by delivery outcome (single-review orders)")
    ax.legend(loc="upper left")
    save(fig, "01_score_distribution")

    # F2 bands
    b = res["bands"][res["bands"].variant.str.startswith("P0")]
    labels = ["On time", "1-3 days late", "4-7 days late", "8+ days late"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4.2))
    xs = np.arange(len(b))
    a1.errorbar(xs, b.mean_score, yerr=[b.mean_score - b.mean_lo, b.mean_hi - b.mean_score], fmt="o", color=BLUE, capsize=3)
    for xi, yi in zip(xs, b.mean_score):
        a1.text(xi + 0.1, yi, f"{yi:.2f}", fontsize=8.5, color=INK2, va="center")
    a1.set_ylim(1, 5)
    a1.set_ylabel("Mean review score")
    a1.set_title("Mean score")
    a2.errorbar(xs, b.low_share, yerr=[b.low_share - b.low_share_lo, b.low_share_hi - b.low_share], fmt="o", color=ORANGE, capsize=3)
    for xi, yi in zip(xs, b.low_share):
        a2.text(xi + 0.1, yi, f"{yi:.0%}", fontsize=8.5, color=INK2, va="center")
    a2.set_ylim(0, 1)
    a2.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a2.set_ylabel("Share with 1-2 stars")
    a2.set_title("Low-score share")
    for a in (a1, a2):
        a.set_xlim(-0.4, len(b) - 0.4)
        a.set_xticks(xs)
        a.set_xticklabels([f"{l}\n(n={int(n):,})" for l, n in zip(labels, b.n)], fontsize=8.5)
    fig.suptitle("Review outcomes by lateness severity (95% intervals; single-review orders)", x=0.01, ha="left", fontweight="bold", fontsize=11)
    save(fig, "02_lateness_bands")

    # F3 sensitivity forest
    e = res["effects"].copy()
    wc = res["worst_case_bounds"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 5), sharey=True)
    ys = np.arange(len(e))[::-1]
    a1.errorbar(e.low_diff * 100, ys, xerr=[(e.low_diff - e.low_diff_lo) * 100, (e.low_diff_hi - e.low_diff) * 100], fmt="o", color=ORANGE, capsize=3)
    a1.axhline(-0.8, color=GREY, linewidth=0.8)
    a1.errorbar([np.mean(wc["low_diff_bounds"]) * 100], [-1.5], xerr=[[(np.mean(wc["low_diff_bounds"]) - wc["low_diff_bounds"][0]) * 100], [(wc["low_diff_bounds"][1] - np.mean(wc["low_diff_bounds"])) * 100]],
                fmt="s", color=GREY, capsize=3)
    a1.set_xlabel("Low-score share, late minus on-time (percentage points)")
    a1.set_title("Low-score gap")
    a2.errorbar(e.mean_diff, ys, xerr=[e.mean_diff - e.mean_diff_lo, e.mean_diff_hi - e.mean_diff], fmt="o", color=BLUE, capsize=3)
    a2.axhline(-0.8, color=GREY, linewidth=0.8)
    a2.errorbar([np.mean(wc["mean_diff_bounds"])], [-1.5], xerr=[[np.mean(wc["mean_diff_bounds"]) - wc["mean_diff_bounds"][0]], [wc["mean_diff_bounds"][1] - np.mean(wc["mean_diff_bounds"])]],
                fmt="s", color=GREY, capsize=3)
    a2.set_xlabel("Mean score, late minus on-time (stars)")
    a2.set_title("Mean-score gap")
    a1.set_yticks(list(ys) + [-1.5])
    a1.set_yticklabels(list(e.variant.str.replace("Multi-review rule: ", "Multi: ", regex=False)) + ["Worst-case bounds (all unreviewed/multi-review orders)"], fontsize=8)
    fig.suptitle("Sensitivity of the late vs on-time review gap (95% cluster-bootstrap intervals)", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "03_sensitivity_gap")

    # F4 adjusted association forest
    mo = res["models"].copy()
    st = res["stability"].copy()
    lab = list(mo.model) + [""] + list(st.model)
    vals = pd.concat([mo[["odds_ratio", "or_lo", "or_hi"]], pd.DataFrame([[np.nan] * 3], columns=["odds_ratio", "or_lo", "or_hi"]), st[["odds_ratio", "or_lo", "or_hi"]]], ignore_index=True)
    ys = np.arange(len(vals))[::-1]
    fig, ax = plt.subplots(figsize=(10, 5))
    ok = vals.odds_ratio.notna().to_numpy()
    ax.errorbar(vals.odds_ratio[ok], ys[ok], xerr=[(vals.odds_ratio - vals.or_lo)[ok], (vals.or_hi - vals.odds_ratio)[ok]], fmt="o", color=BLUE, capsize=3)
    ax.set_xscale("log")
    ax.axvline(1, color=GREY, linewidth=0.8)
    ax.set_yticks(ys)
    ax.set_yticklabels(lab, fontsize=8)
    ax.set_xticks([1, 2, 5, 10, 20])
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel("Odds ratio for a 1-2 star review, late vs on time (log scale, 95% cluster-robust CI)")
    ax.set_title("Adjusted association between lateness and low review scores (not causal)")
    ax.grid(axis="y", visible=False)
    save(fig, "04_adjusted_association")

    # F5 composition of low-score orders
    c = res["low_score_band_composition"]
    fig, ax = plt.subplots(figsize=(9, 2.9))
    colors = [BLUE, "#f4b49a", "#ee8b64", ORANGE]
    for row, (col, name) in enumerate((("share_of_reviewed_orders", "All reviewed orders"), ("share_of_low_score_orders", "1-2 star orders"))):
        left = 0
        for i, v in enumerate(c[col]):
            ax.barh(row, v, left=left, color=colors[i], edgecolor=dr.SURFACE, linewidth=1.5, label=labels[i] if row == 0 else None)
            if v > 0.04:
                ax.text(left + v / 2, row, f"{v:.0%}", ha="center", va="center", fontsize=8.5, color=INK2 if i in (1, 2) else "white")
            left += v
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["All reviewed\norders", "1-2 star\norders"])
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlim(0, 1)
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    ax.set_title("Delivery outcome of reviewed orders vs of low-score reviewed orders (descriptive composition)")
    save(fig, "05_low_score_composition")

    # F6 monthly low-score share
    mm = res["monthly"].copy()
    mm["x"] = pd.to_datetime(mm.purchase_month)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for late, color, label in ((False, BLUE, "On time"), (True, ORANGE, "Late")):
        s = mm[mm.is_late == late]
        ax.fill_between(s.x, s.low_share_lo, s.low_share_hi, color=color, alpha=0.15, linewidth=0)
        ax.plot(s.x, s.low_share, color=color, marker="o", label=label)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_ylim(0, 1)
    ax.set_ylabel("Share of reviewed orders with 1-2 stars")
    ax.set_title("Low-score share by purchase month, on-time vs late (95% Wilson bands)")
    ax.legend(loc="upper right")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m"))
    ax.set_xticks(mm.x.unique()[::2])
    plt.setp(ax.get_xticklabels(), rotation=60, ha="right")
    save(fig, "06_monthly_low_share")

    # F7 review timing: who writes before the recorded delivery, and how do they score?
    et, tg = res["early_timing"], res["timing_groups"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw={"width_ratios": [1, 1.15]})
    xs = np.arange(len(et))
    a1.errorbar(xs, et.early_share, yerr=[et.early_share - et.early_share_lo, et.early_share_hi - et.early_share], fmt="o", color=BLUE, capsize=3)
    for xi, yi in zip(xs, et.early_share):
        a1.text(xi + 0.1, yi, f"{yi:.1%}" if yi < 0.05 else f"{yi:.0%}", fontsize=8.5, color=INK2, va="center")
    a1.set_xticks(xs)
    a1.set_xticklabels([f"{l}\n(n={int(n):,})" for l, n in zip(labels, et.n)], fontsize=8)
    a1.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a1.set_ylim(0, 1.05)
    a1.set_ylabel("Reviews written BEFORE the recorded delivery date")
    a1.set_title("How often the review precedes delivery")
    ys = np.arange(len(tg))[::-1]
    a2.errorbar(tg.low_share, ys, xerr=[tg.low_share - tg.low_lo, tg.low_hi - tg.low_share], fmt="o", color=ORANGE, capsize=3)
    for yi, (v, hi_, n_) in zip(ys, zip(tg.low_share, tg.low_hi, tg.n)):
        a2.text(min(hi_ + 0.025, 0.80), yi, f"{v:.0%} (n={int(n_):,})", fontsize=8.5, color=INK2, va="center")
    a2.set_ylim(-0.6, len(tg) - 0.4)
    a2.set_yticks(ys)
    a2.set_yticklabels(tg.group, fontsize=8.5)
    a2.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a2.set_xlim(0, 1)
    a2.grid(axis="y", visible=False)
    a2.set_xlabel("Share with 1-2 stars (95% Wilson)")
    a2.set_title("Low-score share by delivery outcome and review timing")
    fig.suptitle("Review timing relative to the recorded delivery date (single-review orders)", x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    save(fig, "07_review_timing")
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
