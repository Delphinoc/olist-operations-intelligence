"""Reconciliation anchors taken from earlier phase reports. Never edit a value to make a test pass."""

# Sources in the comments: profile_stats.json, kpi_validation_stats.json, dataset_feasibility.md
ANCHORS = dict(
    orders=99_441, items=112_650, multi_seller_orders=1_278,                      # profile_stats.json
    delivered_dated_all=96_470, late_all=6_534, late_exact_all=7_826,              # kpi_validation q1
    delivered_dated_window=96_203, single_seller_window=94_931, sellers_window=2_925,  # kpi_validation q6
    sellers_ge30=614, sellers_ge50=412, sellers_ge100=201,                          # kpi_validation q6
    ts_violations=1_382, status_date_conflicts=14,                                   # kpi_validation q5 / feasibility
    review_p0_all=95_299, review_zero_all=646, review_multi_all=525,                # kpi_validation q2
    ontime_n_all=88_946, late_n_all=6_353, early_review_all=4_940,                  # kpi_validation q3
    multi_review_orders=547, delivered_one_seller_all=95_195, delivered_multi_seller_all=1_275,
)
LATE_RATE_WINDOW_DOC = 0.0679   # "approximately 6.79%"; tolerance below allows any value that rounds to 6.79%


