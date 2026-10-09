"""Python equivalents of the Power BI DAX measures, evaluated on the EXPORTED CSV package, plus an independent SQL reference.

Purpose: validate the measure dictionary (powerbi/dax_measures.md) and the relationship design BEFORE a .pbix exists.
`PowerBIModel` mimics the semantic model: dimension filters propagate to fact tables along the single-direction
relationships (dim -> fact) and nowhere else; the two fact tables never filter each other; snapshot tables are disconnected.
`measures_orders` / `measures_seller` mirror the DAX formulas one for one (DIVIDE -> None on a zero denominator).
`sql_reference` computes the same quantities directly from the validated DuckDB model with SQL, as the independent check.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

Z95 = 1.959963984540054
MIN_STATE_N = 100


def divide(a, b):
    """DAX DIVIDE: BLANK (None) when the denominator is zero or blank."""
    return None if (b is None or b == 0 or a is None) else a / b


def wilson(k, n):
    """Wilson 95% interval written exactly as in the DAX measures."""
    if not n:
        return (None, None)
    p = k / n
    z = Z95
    centre = p + z ** 2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    d = 1 + z ** 2 / n
    return ((centre - half) / d, (centre + half) / d)


class PowerBIModel:
    """Loads the CSV package and applies slicer-style filters through the documented relationships."""

    def __init__(self, data_dir: Path):
        d = Path(data_dir)
        self.fact_orders = pd.read_csv(d / "fact_orders.csv", parse_dates=["purchase_date"])
        self.fact_seller_orders = pd.read_csv(d / "fact_seller_orders.csv", parse_dates=["purchase_date"])
        self.dim_date = pd.read_csv(d / "dim_date.csv", parse_dates=["date_key", "month_start"])
        self.dim_state = pd.read_csv(d / "dim_state.csv")
        self.dim_origin_state = pd.read_csv(d / "dim_origin_state.csv")
        self.dim_seller = pd.read_csv(d / "dim_seller.csv")
        self.snap = {n: pd.read_csv(d / f"{n}.csv") for n in ("snap_priority_candidates", "snap_tier_counts", "snap_overlap", "snap_metadata")}

    # ---- slicer semantics -------------------------------------------------------------------------
    def _date_keys(self, months):
        if months is None:
            return None
        lo, hi = months
        dd = self.dim_date[(self.dim_date.year_month >= lo) & (self.dim_date.year_month <= hi)]
        return set(dd.date_key)

    def _state_codes(self, customer_state, macro_region):
        codes = set(self.dim_state.state_code)
        if customer_state is not None:
            codes &= set(customer_state)
        if macro_region is not None:
            codes &= set(self.dim_state[self.dim_state.macro_region.isin(macro_region)].state_code)
        return codes

    def orders(self, months=None, customer_state=None, macro_region=None, **ignored) -> pd.DataFrame:
        """fact_orders under a filter context. origin_state / seller_id are IGNORED: no relationship exists to that fact."""
        f = self.fact_orders
        keys = self._date_keys(months)
        if keys is not None:
            f = f[f.purchase_date.isin(keys)]
        if customer_state is not None or macro_region is not None:
            f = f[f.customer_state.isin(self._state_codes(customer_state, macro_region))]
        return f

    def seller_orders(self, months=None, customer_state=None, macro_region=None, origin_state=None, origin_region=None, seller_id=None) -> pd.DataFrame:
        f = self.fact_seller_orders
        keys = self._date_keys(months)
        if keys is not None:
            f = f[f.purchase_date.isin(keys)]
        if customer_state is not None or macro_region is not None:
            f = f[f.customer_state.isin(self._state_codes(customer_state, macro_region))]
        if origin_state is not None or origin_region is not None:
            o = self.dim_origin_state
            if origin_state is not None:
                o = o[o.origin_state_code.isin(origin_state)]
            if origin_region is not None:
                o = o[o.origin_macro_region.isin(origin_region)]
            f = f[f.seller_state.isin(set(o.origin_state_code))]
        if seller_id is not None:
            f = f[f.seller_id.isin(seller_id)]
        return f


# ---------------------------------------------------------------------------------------------------
def measures_orders(df: pd.DataFrame) -> dict:
    """The fact_orders measures of powerbi/dax_measures.md, one for one."""
    m: dict = {}
    m["All orders in file"] = len(df)
    inwin = df[df.in_window == 1]
    m["Orders in window"] = len(inwin)
    d = df[df.is_delivery_kpi_eligible == 1]
    m["Delivered orders"] = len(d)
    m["Late orders"] = int((d.is_late == 1).sum())
    m["On-time orders"] = m["Delivered orders"] - m["Late orders"]
    m["Late-delivery rate"] = divide(m["Late orders"], m["Delivered orders"])
    m["Severe-late orders"] = int((d.is_severe_late == 1).sum())
    m["Severe-late rate"] = divide(m["Severe-late orders"], m["Delivered orders"])
    m["Median lead time (days)"] = float(d.lead_time_days.median()) if len(d) else None
    m["P95 lead time (days)"] = float(np.percentile(d.lead_time_days.dropna(), 95)) if len(d) else None       # PERCENTILE.INC = linear interpolation
    m["Median promise error (days)"] = float(d.promise_error_days.median()) if len(d) else None
    m["Median promised lead time (days)"] = float(d.promised_lead_days.median()) if len(d) else None
    lo, hi = wilson(m["Late orders"], m["Delivered orders"])
    m["Late-delivery rate, Wilson lower"], m["Late-delivery rate, Wilson upper"] = lo, hi
    for label, cls in (("Cancelled/unavailable orders", "cancelled_unavailable"), ("Open past-promise orders", "open_past_promise"),
                       ("Open not-yet-due orders", "open_not_yet_due"), ("Delivered-status, no-date orders", "delivered_status_no_date")):
        m[label] = int((inwin.fulfilment_class == cls).sum())
    m["Cancelled/unavailable share"] = divide(m["Cancelled/unavailable orders"], m["Orders in window"])
    m["Open past-promise share"] = divide(m["Open past-promise orders"], m["Orders in window"])
    # reviews: P0 = exactly one review row; P1 = P0 minus reviews created before delivery
    p0 = df[df.is_review_p0 == 1]
    p1 = df[df.is_review_p1 == 1]
    m["Reviewed orders (P0)"] = len(p0)
    m["P0 share of delivered orders"] = divide(len(p0), len(d))
    m["Review coverage (any review row)"] = divide(int((d.review_row_count >= 1).sum()), len(d))
    m["No-review orders"] = int((d.review_row_count == 0).sum())
    m["Multi-review orders"] = int((d.review_row_count > 1).sum())
    m["Average review score (P0)"] = float(p0.review_score.mean()) if len(p0) else None
    m["Low-score orders (P0)"] = int((p0.review_score <= 2).sum())
    m["Low-score share (P0)"] = divide(m["Low-score orders (P0)"], len(p0))
    m["Reviewed orders (P1)"] = len(p1)
    m["Average review score (P1)"] = float(p1.review_score.mean()) if len(p1) else None
    m["Low-score share (P1)"] = divide(int((p1.review_score <= 2).sum()), len(p1))
    m["Reviews written before delivery (P0)"] = int((p0.review_before_delivery_flag == 1).sum())
    m["Share of P0 reviews written before delivery"] = divide(m["Reviews written before delivery (P0)"], len(p0))
    on, late = p0[p0.is_late == 0], p0[p0.is_late == 1]
    m["Low-score share, on-time orders (P0)"] = divide(int((on.review_score <= 2).sum()), len(on))
    m["Low-score share, late orders (P0)"] = divide(int((late.review_score <= 2).sum()), len(late))
    m["Average review score, on-time (P0)"] = float(on.review_score.mean()) if len(on) else None
    m["Average review score, late (P0)"] = float(late.review_score.mean()) if len(late) else None
    return m


def measures_seller(df: pd.DataFrame) -> dict:
    """The fact_seller_orders measures (seller-specific measures exist ONLY on this fact)."""
    m: dict = {}
    m["Seller: eligible orders"] = len(df)
    m["Seller: late orders"] = int((df.is_late == 1).sum())
    m["Seller: late-delivery rate"] = divide(m["Seller: late orders"], len(df))
    m["Seller: severe-late rate"] = divide(int((df.is_severe_late == 1).sum()), len(df))
    m["Seller: median lead time (days)"] = float(df.lead_time_days.median()) if len(df) else None
    m["Seller: P95 lead time (days)"] = float(np.percentile(df.lead_time_days.dropna(), 95)) if len(df) else None
    m["Seller: cross-state share"] = divide(int((df.is_cross_state == 1).sum()), len(df))
    m["Seller: median distance (km)"] = float(df.distance_km.median()) if df.distance_km.notna().any() else None
    cs, ss = df[df.is_cross_state == 1], df[df.is_cross_state == 0]
    m["Seller: late rate, cross-state"] = divide(int((cs.is_late == 1).sum()), len(cs))
    m["Seller: late rate, same-state"] = divide(int((ss.is_late == 1).sum()), len(ss))
    p0 = df[df.is_review_p0 == 1]
    m["Seller: reviewed orders (P0)"] = len(p0)
    m["Seller: average review score (P0)"] = float(p0.review_score.mean()) if len(p0) else None
    m["Seller: low-score share (P0)"] = divide(int((p0.review_score <= 2).sum()), len(p0))
    lo, hi = wilson(m["Seller: late orders"], len(df))
    m["Seller: late-delivery rate, Wilson lower"], m["Seller: late-delivery rate, Wilson upper"] = lo, hi
    return m


# ---------------------------------------------------------------------------------------------------
# Independent SQL reference on the validated DuckDB model (never reads the exported CSVs)
def _where(alias_state: str, months, customer_state, macro_region, month_col: str, extra: list[str]) -> str:
    c = []
    if months is not None:
        c.append(f"{month_col} BETWEEN DATE '{months[0]}-01' AND DATE '{months[1]}-01'")
    if customer_state is not None:
        c.append(f"{alias_state}.state_code IN ({', '.join(repr(s) for s in customer_state)})")
    if macro_region is not None:
        c.append(f"{alias_state}.macro_region IN ({', '.join(repr(s) for s in macro_region)})")
    c += extra
    return ("WHERE " + " AND ".join(c)) if c else ""


def sql_reference_orders(con, months=None, customer_state=None, macro_region=None, **ignored) -> dict:
    w = _where("s", months, customer_state, macro_region, "f.purchase_month", [])
    r = con.execute(f"""
        SELECT count(*) AS all_orders,
               count(*) FILTER (WHERE f.in_window) AS inwin,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible) AS delivered,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible AND f.is_late_calendar) AS late,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible AND f.is_severe_late) AS severe,
               median(f.lead_time_days) FILTER (WHERE f.is_delivery_kpi_eligible) AS med_lead,
               quantile_cont(f.lead_time_days, 0.95) FILTER (WHERE f.is_delivery_kpi_eligible) AS p95_lead,
               median(f.promise_error_days) FILTER (WHERE f.is_delivery_kpi_eligible) AS med_err,
               median(f.promised_lead_days) FILTER (WHERE f.is_delivery_kpi_eligible) AS med_prom,
               count(*) FILTER (WHERE f.in_window AND f.fulfilment_class = 'cancelled_unavailable') AS canc,
               count(*) FILTER (WHERE f.in_window AND f.fulfilment_class = 'open_past_promise') AS openp,
               count(*) FILTER (WHERE f.in_window AND f.fulfilment_class = 'open_not_yet_due') AS opennd,
               count(*) FILTER (WHERE f.in_window AND f.fulfilment_class = 'delivered_status_no_date') AS nodate,
               count(*) FILTER (WHERE f.is_review_kpi_eligible) AS p0,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible AND f.review_row_count >= 1) AS anyrev,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible AND f.review_row_count = 0) AS norev,
               count(*) FILTER (WHERE f.is_delivery_kpi_eligible AND f.review_row_count > 1) AS multirev,
               avg(f.review_score) FILTER (WHERE f.is_review_kpi_eligible) AS avg_p0,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND f.review_score <= 2) AS low_p0,
               count(*) FILTER (WHERE f.is_review_p1_eligible) AS p1,
               avg(f.review_score) FILTER (WHERE f.is_review_p1_eligible) AS avg_p1,
               count(*) FILTER (WHERE f.is_review_p1_eligible AND f.review_score <= 2) AS low_p1,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND f.review_before_delivery_flag) AS early,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND NOT f.is_late_calendar) AS p0_on,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND NOT f.is_late_calendar AND f.review_score <= 2) AS low_on,
               avg(f.review_score) FILTER (WHERE f.is_review_kpi_eligible AND NOT f.is_late_calendar) AS avg_on,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND f.is_late_calendar) AS p0_late,
               count(*) FILTER (WHERE f.is_review_kpi_eligible AND f.is_late_calendar AND f.review_score <= 2) AS low_late,
               avg(f.review_score) FILTER (WHERE f.is_review_kpi_eligible AND f.is_late_calendar) AS avg_late
        FROM fact_orders f JOIN dim_state s ON s.state_code = f.customer_state {w}""").fetchdf().iloc[0]
    g = lambda k: (None if pd.isna(r[k]) else float(r[k]))  # noqa: E731
    m = {"All orders in file": int(r.all_orders), "Orders in window": int(r.inwin), "Delivered orders": int(r.delivered), "Late orders": int(r.late),
         "On-time orders": int(r.delivered - r.late), "Late-delivery rate": divide(int(r.late), int(r.delivered)), "Severe-late orders": int(r.severe),
         "Severe-late rate": divide(int(r.severe), int(r.delivered)), "Median lead time (days)": g("med_lead"), "P95 lead time (days)": g("p95_lead"),
         "Median promise error (days)": g("med_err"), "Median promised lead time (days)": g("med_prom"),
         "Cancelled/unavailable orders": int(r.canc), "Open past-promise orders": int(r.openp), "Open not-yet-due orders": int(r.opennd), "Delivered-status, no-date orders": int(r.nodate),
         "Cancelled/unavailable share": divide(int(r.canc), int(r.inwin)), "Open past-promise share": divide(int(r.openp), int(r.inwin)),
         "Reviewed orders (P0)": int(r.p0), "P0 share of delivered orders": divide(int(r.p0), int(r.delivered)),
         "Review coverage (any review row)": divide(int(r.anyrev), int(r.delivered)), "No-review orders": int(r.norev), "Multi-review orders": int(r.multirev),
         "Average review score (P0)": g("avg_p0"), "Low-score orders (P0)": int(r.low_p0), "Low-score share (P0)": divide(int(r.low_p0), int(r.p0)),
         "Reviewed orders (P1)": int(r.p1), "Average review score (P1)": g("avg_p1"), "Low-score share (P1)": divide(int(r.low_p1), int(r.p1)),
         "Reviews written before delivery (P0)": int(r.early), "Share of P0 reviews written before delivery": divide(int(r.early), int(r.p0)),
         "Low-score share, on-time orders (P0)": divide(int(r.low_on), int(r.p0_on)), "Low-score share, late orders (P0)": divide(int(r.low_late), int(r.p0_late)),
         "Average review score, on-time (P0)": g("avg_on"), "Average review score, late (P0)": g("avg_late")}
    return m


def sql_reference_seller(con, months=None, customer_state=None, macro_region=None, origin_state=None, origin_region=None, seller_id=None) -> dict:
    extra = []
    if origin_state is not None:
        extra.append(f"v.seller_state IN ({', '.join(repr(s) for s in origin_state)})")
    if origin_region is not None:
        extra.append(f"os.macro_region IN ({', '.join(repr(s) for s in origin_region)})")
    if seller_id is not None:
        extra.append(f"v.seller_id IN ({', '.join(repr(s) for s in seller_id)})")
    w = _where("s", months, customer_state, macro_region, "v.purchase_month", extra)
    r = con.execute(f"""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE v.is_late_calendar) AS late,
               count(*) FILTER (WHERE v.is_severe_late) AS severe,
               median(v.lead_time_days) AS med_lead, quantile_cont(v.lead_time_days, 0.95) AS p95_lead,
               count(*) FILTER (WHERE v.is_cross_state) AS cross_n,
               median(v.distance_km) AS med_dist,
               count(*) FILTER (WHERE v.is_cross_state AND v.is_late_calendar) AS cross_late,
               count(*) FILTER (WHERE NOT v.is_cross_state) AS same_n,
               count(*) FILTER (WHERE NOT v.is_cross_state AND v.is_late_calendar) AS same_late,
               count(*) FILTER (WHERE v.review_row_count = 1) AS p0,
               avg(v.review_score) FILTER (WHERE v.review_row_count = 1) AS avg_p0,
               count(*) FILTER (WHERE v.review_row_count = 1 AND v.review_score <= 2) AS low_p0
        FROM v_single_seller_orders v
        JOIN dim_state s ON s.state_code = v.customer_state
        JOIN dim_state os ON os.state_code = v.seller_state {w}""").fetchdf().iloc[0]
    g = lambda k: (None if pd.isna(r[k]) else float(r[k]))  # noqa: E731
    n = int(r.n)
    return {"Seller: eligible orders": n, "Seller: late orders": int(r.late), "Seller: late-delivery rate": divide(int(r.late), n),
            "Seller: severe-late rate": divide(int(r.severe), n), "Seller: median lead time (days)": g("med_lead"), "Seller: P95 lead time (days)": g("p95_lead"),
            "Seller: cross-state share": divide(int(r.cross_n), n), "Seller: median distance (km)": g("med_dist"),
            "Seller: late rate, cross-state": divide(int(r.cross_late), int(r.cross_n)), "Seller: late rate, same-state": divide(int(r.same_late), int(r.same_n)),
            "Seller: reviewed orders (P0)": int(r.p0), "Seller: average review score (P0)": g("avg_p0"), "Seller: low-score share (P0)": divide(int(r.low_p0), int(r.p0))}


SCENARIOS: list[tuple[str, dict]] = [
    ("No filter (all dates)", {}),
    ("Customer state = RJ", {"customer_state": ["RJ"]}),
    ("Region = Northeast", {"macro_region": ["Northeast"]}),
    ("Months 2017-11..2018-03 (date slicer)", {"months": ("2017-11", "2018-03")}),
    ("State RJ and months 2017-11..2018-03", {"customer_state": ["RJ"], "months": ("2017-11", "2018-03")}),
    ("Analysis window months 2017-01..2018-08", {"months": ("2017-01", "2018-08")}),
    ("Region Southeast and months 2018-01..2018-08", {"macro_region": ["Southeast"], "months": ("2018-01", "2018-08")}),
    ("Origin state SP and customer state RJ (lane SP>RJ)", {"origin_state": ["SP"], "customer_state": ["RJ"]}),
    ("Origin region Southeast, customer region Northeast", {"origin_region": ["Southeast"], "macro_region": ["Northeast"]}),
]
