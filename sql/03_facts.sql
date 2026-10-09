-- 03_facts.sql: fact tables, seller bridge, single-seller view.
-- Rule: every one-to-many source (items, reviews) is aggregated to order grain BEFORE it is joined
-- to the order-grain fact. Geolocation is reduced to one row per ZIP prefix (dim_zip_geo) before any join.

-- ---------------------------------------------------------------------------------------------
-- fact_order_items: grain = one row per (order_id, order_item_id)
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE fact_order_items AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    i.shipping_limit_date                   AS shipping_limit_ts,
    i.price,
    i.freight_value,
    pr.product_category,                    -- English (or Portuguese fallback); NULL if missing
    c.customer_state,
    s.seller_state,
    c.customer_zip_code_prefix              AS customer_zip_prefix,
    s.seller_zip_code_prefix                AS seller_zip_prefix,
    (c.customer_state <> s.seller_state)    AS is_cross_state,
    -- haversine great-circle distance between ZIP-prefix centroids (NULL if either centroid missing)
    CASE WHEN gc.lat IS NOT NULL AND gs.lat IS NOT NULL THEN
        2 * p.earth_radius_km * asin(sqrt(least(1.0,
            power(sin(radians(gs.lat - gc.lat) / 2), 2)
            + cos(radians(gc.lat)) * cos(radians(gs.lat)) * power(sin(radians(gs.lng - gc.lng) / 2), 2))))
    END                                     AS distance_km
FROM stg_order_items i
LEFT JOIN stg_orders    o  ON o.order_id    = i.order_id
LEFT JOIN stg_customers c  ON c.customer_id = o.customer_id
LEFT JOIN stg_sellers   s  ON s.seller_id   = i.seller_id
LEFT JOIN dim_product   pr ON pr.product_id = i.product_id
LEFT JOIN dim_zip_geo   gc ON gc.zip_code_prefix = c.customer_zip_code_prefix   -- unique per prefix
LEFT JOIN dim_zip_geo   gs ON gs.zip_code_prefix = s.seller_zip_code_prefix      -- unique per prefix
CROSS JOIN model_params p;

-- ---------------------------------------------------------------------------------------------
-- bridge_order_seller: grain = one row per (order_id, seller_id). DuckDB validation only.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE bridge_order_seller AS
SELECT
    order_id,
    seller_id,
    count(*)                              AS n_items_from_seller,
    sum(price)                            AS items_value_from_seller,
    sum(freight_value)                    AS freight_value_from_seller,
    count(*) OVER (PARTITION BY order_id) AS n_sellers_in_order,
    (count(*) OVER (PARTITION BY order_id) = 1) AS is_single_seller
FROM fact_order_items
GROUP BY order_id, seller_id;

-- ---------------------------------------------------------------------------------------------
-- fact_reviews: grain = one row per (order_id, review_id). No "latest review" flag by design:
-- multi-review orders are ambiguous (updates vs duplicates cannot be told apart).
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE fact_reviews AS
SELECT
    order_id,
    review_id,
    review_score,
    (review_comment_title   IS NOT NULL) AS has_comment_title,
    (review_comment_message IS NOT NULL) AS has_comment_message,
    CAST(review_creation_date AS DATE)   AS review_creation_date,
    review_answer_timestamp              AS review_answer_ts,
    count(*) OVER (PARTITION BY order_id) AS review_rows_in_order
FROM stg_reviews;

-- ---------------------------------------------------------------------------------------------
-- fact_orders: grain = one row per order_id; ALL orders are kept, eligibility is expressed as flags.
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE TABLE fact_orders AS
WITH items_by_order AS (                       -- one-to-many -> order grain
    SELECT
        order_id,
        count(*)                                                  AS n_items,
        count(DISTINCT seller_id)                                 AS n_sellers,
        sum(price)                                                AS items_value,
        sum(freight_value)                                        AS freight_value,
        -- seller attribution only when exactly one seller is involved
        CASE WHEN count(DISTINCT seller_id) = 1 THEN min(seller_id)    END AS primary_seller_id,
        CASE WHEN count(DISTINCT seller_id) = 1 THEN min(seller_state) END AS seller_state,
        CASE WHEN count(DISTINCT seller_id) = 1 THEN median(distance_km) END AS distance_km_median,
        -- order_category: single category if all items share one; 'unknown' if none known; else 'mixed'
        -- (items with a missing category alongside a known one count as 'mixed')
        CASE WHEN count(*) FILTER (WHERE product_category IS NOT NULL) = 0 THEN 'unknown'
             WHEN count(DISTINCT product_category) = 1
                  AND count(*) FILTER (WHERE product_category IS NULL) = 0 THEN min(product_category)
             ELSE 'mixed' END                                     AS order_category
    FROM fact_order_items
    GROUP BY order_id
), reviews_by_order AS (                       -- one-to-many -> order grain
    SELECT
        order_id,
        count(*)                                                  AS review_row_count,
        -- score/date exist ONLY when exactly one review row exists
        CASE WHEN count(*) = 1 THEN max(review_score)         END AS review_score,
        CASE WHEN count(*) = 1 THEN max(review_creation_date) END AS review_creation_date
    FROM fact_reviews
    GROUP BY order_id
), base AS (
    SELECT
        o.order_id,
        o.customer_id,
        c.customer_unique_id,
        c.customer_state,
        c.customer_zip_code_prefix                    AS customer_zip_prefix,
        o.order_status,
        o.order_purchase_timestamp                    AS purchase_ts,
        o.order_approved_at                           AS approved_ts,
        o.order_delivered_carrier_date                AS carrier_ts,
        o.order_delivered_customer_date               AS delivered_customer_ts,
        CAST(o.order_purchase_timestamp AS DATE)      AS purchase_date,
        CAST(date_trunc('month', o.order_purchase_timestamp) AS DATE) AS purchase_month,
        CAST(o.order_estimated_delivery_date AS DATE) AS estimated_date,
        CAST(o.order_delivered_customer_date AS DATE) AS delivered_date,
        coalesce(i.n_items, 0)                        AS n_items,
        coalesce(i.n_sellers, 0)                      AS n_sellers,
        i.items_value,
        i.freight_value,
        i.primary_seller_id,
        i.seller_state,
        i.distance_km_median,
        i.order_category,
        coalesce(r.review_row_count, 0)               AS review_row_count,
        r.review_score,
        r.review_creation_date,
        p.window_start, p.window_end_exclusive, p.reference_date, p.severe_late_days
    FROM stg_orders o
    LEFT JOIN stg_customers   c ON c.customer_id = o.customer_id
    LEFT JOIN items_by_order  i ON i.order_id    = o.order_id
    LEFT JOIN reviews_by_order r ON r.order_id   = o.order_id
    CROSS JOIN model_params p
), flagged AS (
    SELECT
        b.*,
        (purchase_date >= window_start AND purchase_date < window_end_exclusive)       AS in_window,
        (order_status = 'delivered' AND delivered_customer_ts IS NOT NULL)             AS is_delivered_dated,
        (order_status = 'delivered' AND delivered_customer_ts IS NOT NULL
            AND delivered_date > estimated_date)                                       AS is_late_calendar_raw
    FROM base b
)
SELECT
    order_id,
    customer_id,
    customer_unique_id,
    customer_state,
    customer_zip_prefix,
    order_status,
    purchase_ts, approved_ts, carrier_ts, delivered_customer_ts,
    purchase_date, purchase_month, estimated_date, delivered_date,

    -- population flags
    in_window,
    is_delivered_dated,

    -- blueprint R9: six mutually exclusive fulfilment classes (all orders, any window)
    CASE
        WHEN is_delivered_dated AND is_late_calendar_raw        THEN 'delivered_late'
        WHEN is_delivered_dated                                 THEN 'delivered_on_time'
        WHEN order_status = 'delivered'                         THEN 'delivered_status_no_date'
        WHEN order_status IN ('canceled', 'unavailable')        THEN 'cancelled_unavailable'
        WHEN order_status IN ('created', 'approved', 'invoiced', 'processing', 'shipped')
             AND estimated_date <  reference_date               THEN 'open_past_promise'
        WHEN order_status IN ('created', 'approved', 'invoiced', 'processing', 'shipped')
             AND estimated_date >= reference_date               THEN 'open_not_yet_due'
        ELSE 'unclassified'                                      -- must never occur (asserted)
    END AS fulfilment_class,
    -- status vs delivery-timestamp contradictions (delivered w/o date; non-delivered with date)
    ((order_status = 'delivered') <> (delivered_customer_ts IS NOT NULL)) AS has_status_date_conflict,

    -- delivery measures: NULL unless delivered with a delivery timestamp (R1: calendar-date lateness)
    CASE WHEN is_delivered_dated THEN (epoch(delivered_customer_ts) - epoch(purchase_ts)) / 86400.0 END AS lead_time_days,
    CASE WHEN is_delivered_dated THEN date_diff('day', purchase_date, estimated_date)  END AS promised_lead_days,
    CASE WHEN is_delivered_dated THEN date_diff('day', estimated_date, delivered_date) END AS promise_error_days,
    CASE WHEN is_delivered_dated THEN is_late_calendar_raw END                               AS is_late_calendar,
    CASE WHEN is_delivered_dated THEN date_diff('day', estimated_date, delivered_date) > severe_late_days END AS is_severe_late,
    -- exact-timestamp lateness: sensitivity only (estimated date is midnight, so it over-counts)
    CASE WHEN is_delivered_dated THEN delivered_customer_ts > CAST(estimated_date AS TIMESTAMP) END AS is_late_exact,
    CASE WHEN is_delivered_dated THEN
        CASE WHEN date_diff('day', estimated_date, delivered_date) <= 0 THEN '<=0'
             WHEN date_diff('day', estimated_date, delivered_date) <= 3 THEN '1-3'
             WHEN date_diff('day', estimated_date, delivered_date) <= 7 THEN '4-7'
             ELSE '8+' END
    END AS days_late_band,

    -- timestamp-sequence anomalies (blueprint R7): flagged, never deleted
    -- coalesce: a comparison against a missing timestamp is NULL; "no violation established" must be FALSE,
    -- otherwise `WHERE NOT ts_sequence_violation` silently drops those orders
    coalesce(approved_ts < purchase_ts OR carrier_ts < purchase_ts OR carrier_ts < approved_ts
        OR delivered_customer_ts < purchase_ts OR delivered_customer_ts < carrier_ts
        OR CAST(estimated_date AS TIMESTAMP) < purchase_ts, FALSE)           AS ts_sequence_violation,
    coalesce(carrier_ts < purchase_ts OR carrier_ts < approved_ts OR delivered_customer_ts < carrier_ts, FALSE)
                                                                             AS carrier_leg_unreliable,

    -- items / seller attribution
    n_items, n_sellers, (n_sellers = 1) AS is_single_seller,
    items_value, freight_value,
    primary_seller_id,                           -- NULL unless exactly one seller
    seller_state,                                -- NULL unless exactly one seller
    CASE WHEN n_sellers = 1 THEN seller_state <> customer_state END AS is_cross_state,   -- NULL unless one seller
    distance_km_median,
    order_category,

    -- reviews (score only when exactly one review row exists)
    review_row_count,
    review_score,
    review_creation_date,
    CASE WHEN review_row_count = 1 AND is_delivered_dated THEN review_creation_date < delivered_date END
                                                                             AS review_before_delivery_flag,

    -- explicit eligibility flags per blueprint populations
    (in_window AND is_delivered_dated)                                       AS is_delivery_kpi_eligible,
    (in_window AND is_delivered_dated AND n_sellers = 1)                     AS is_seller_kpi_eligible,
    (in_window AND is_delivered_dated AND review_row_count = 1)              AS is_review_kpi_eligible,
    -- sensitivity P1: reviews created strictly before the delivery date removed
    (in_window AND is_delivered_dated AND review_row_count = 1
        AND NOT (review_creation_date < delivered_date))                     AS is_review_p1_eligible
FROM flagged;

-- ---------------------------------------------------------------------------------------------
-- v_single_seller_orders: grain = one row per eligible order (window, delivered with date, one seller).
-- No multi-seller attribution is possible by construction (primary_seller_id is non-null here).
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_single_seller_orders AS
SELECT
    order_id,
    customer_unique_id,
    primary_seller_id          AS seller_id,
    seller_state,
    customer_state,
    is_cross_state,
    distance_km_median         AS distance_km,
    purchase_date,
    purchase_month,
    estimated_date,
    n_items,
    items_value,
    freight_value,
    order_category,
    lead_time_days,
    promised_lead_days,
    promise_error_days,
    is_late_calendar,
    is_severe_late,
    is_late_exact,
    days_late_band,
    ts_sequence_violation,
    carrier_leg_unreliable,
    review_row_count,
    review_score,
    review_before_delivery_flag
FROM fact_orders
WHERE is_seller_kpi_eligible;
