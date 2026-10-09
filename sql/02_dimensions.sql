-- 02_dimensions.sql: parameters and dimension tables.

-- Single-row parameter table: the one place where window, thresholds and reference date are defined.
CREATE OR REPLACE TABLE model_params AS
SELECT
    DATE '2017-01-01' AS window_start,                -- blueprint R2: purchases 2017-01 ...
    DATE '2018-09-01' AS window_end_exclusive,        -- ... to 2018-08 inclusive
    7                 AS severe_late_days,            -- blueprint R10: promise error > 7 calendar days
    -- blueprint R9: reference date = latest observed event timestamp in the extract
    -- (estimated dates are excluded because they lie in the future of the extract)
    CAST(greatest(
        (SELECT max(order_purchase_timestamp)      FROM stg_orders),
        (SELECT max(order_approved_at)             FROM stg_orders),
        (SELECT max(order_delivered_carrier_date)  FROM stg_orders),
        (SELECT max(order_delivered_customer_date) FROM stg_orders)) AS DATE) AS reference_date,
    -- rough Brazil bounding box used to drop implausible geolocation points. The eastern bound (-28.0) is
    -- deliberately wider than the mainland (-34.79) so Fernando de Noronha (ZIP 53990, lng about -32.4) is kept.
    -33.75 AS geo_lat_min, 5.27 AS geo_lat_max, -73.99 AS geo_lng_min, -28.0 AS geo_lng_max,
    6371.0088 AS earth_radius_km;

CREATE OR REPLACE TABLE dim_date AS
SELECT
    CAST(d AS DATE)                                  AS date_key,
    year(d)                                          AS year,
    month(d)                                         AS month,
    strftime(d, '%Y-%m')                             AS year_month,
    CAST(date_trunc('month', d) AS DATE)             AS month_start,
    isodow(d)                                        AS iso_weekday,
    dayname(d)                                       AS weekday_name,
    (CAST(d AS DATE) >= p.window_start AND CAST(d AS DATE) < p.window_end_exclusive) AS is_in_window
FROM generate_series(TIMESTAMP '2016-01-01', TIMESTAMP '2018-12-31', INTERVAL 1 DAY) AS t(d)
CROSS JOIN model_params p;

-- IBGE macro-regions: EXTERNAL reference data (not derived from the dataset).
-- Verification and sources are documented in reports/data_model_validation.md.
CREATE OR REPLACE TABLE dim_state AS
SELECT * FROM (VALUES
    ('AC', 'Acre',                'North'),
    ('AP', 'Amapa',               'North'),
    ('AM', 'Amazonas',            'North'),
    ('PA', 'Para',                'North'),
    ('RO', 'Rondonia',            'North'),
    ('RR', 'Roraima',             'North'),
    ('TO', 'Tocantins',           'North'),
    ('AL', 'Alagoas',             'Northeast'),
    ('BA', 'Bahia',               'Northeast'),
    ('CE', 'Ceara',               'Northeast'),
    ('MA', 'Maranhao',            'Northeast'),
    ('PB', 'Paraiba',             'Northeast'),
    ('PE', 'Pernambuco',          'Northeast'),
    ('PI', 'Piaui',               'Northeast'),
    ('RN', 'Rio Grande do Norte', 'Northeast'),
    ('SE', 'Sergipe',             'Northeast'),
    ('DF', 'Distrito Federal',    'Central-West'),
    ('GO', 'Goias',               'Central-West'),
    ('MT', 'Mato Grosso',         'Central-West'),
    ('MS', 'Mato Grosso do Sul',  'Central-West'),
    ('ES', 'Espirito Santo',      'Southeast'),
    ('MG', 'Minas Gerais',        'Southeast'),
    ('RJ', 'Rio de Janeiro',      'Southeast'),
    ('SP', 'Sao Paulo',           'Southeast'),
    ('PR', 'Parana',              'South'),
    ('RS', 'Rio Grande do Sul',   'South'),
    ('SC', 'Santa Catarina',      'South')
) AS t(state_code, state_name, macro_region);

-- One row per ZIP prefix. geolocation is NOT a lookup table (1,000,163 rows for 19,015 prefixes), so:
-- (1) DISTINCT coordinates per prefix, (2) drop points outside the bounding box,
-- (3) median lat/lng of the remaining points.
CREATE OR REPLACE TABLE dim_zip_geo AS
WITH points AS (
    SELECT DISTINCT geolocation_zip_code_prefix AS zip_code_prefix,
                    geolocation_lat AS lat, geolocation_lng AS lng
    FROM stg_geolocation
), flagged AS (
    SELECT pt.*,
           (pt.lat BETWEEN p.geo_lat_min AND p.geo_lat_max
            AND pt.lng BETWEEN p.geo_lng_min AND p.geo_lng_max) AS in_bbox
    FROM points pt CROSS JOIN model_params p
)
SELECT zip_code_prefix,
       median(lat) FILTER (WHERE in_bbox) AS lat,
       median(lng) FILTER (WHERE in_bbox) AS lng,
       count(*)                           AS n_distinct_points,
       count(*) FILTER (WHERE in_bbox)    AS n_points_used
FROM flagged
GROUP BY zip_code_prefix
HAVING count(*) FILTER (WHERE in_bbox) > 0;

CREATE OR REPLACE TABLE dim_product AS
SELECT p.product_id,
       p.product_category_name,
       -- English name; Portuguese name where no translation exists; NULL where category is missing
       coalesce(t.product_category_name_english, p.product_category_name) AS product_category,
       (p.product_category_name IS NULL)                                  AS is_category_missing,
       (p.product_category_name IS NOT NULL
        AND t.product_category_name_english IS NULL)                      AS is_translation_missing,
       p.product_name_lenght        AS product_name_length,        -- source column is misspelt
       p.product_description_lenght AS product_description_length,
       p.product_photos_qty, p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm
FROM stg_products p
LEFT JOIN stg_category_translation t USING (product_category_name);

CREATE OR REPLACE TABLE dim_seller AS
SELECT s.seller_id, s.seller_zip_code_prefix, s.seller_city, s.seller_state, st.macro_region AS seller_macro_region
FROM stg_sellers s
LEFT JOIN dim_state st ON st.state_code = s.seller_state;
