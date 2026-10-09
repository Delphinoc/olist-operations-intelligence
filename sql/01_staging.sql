-- 01_staging.sql: typed, otherwise unmodified copies of the raw CSVs (source files are read-only).
-- Placeholder {{RAW_DIR}} is replaced by scripts/build_model.py with a path relative to the project root.

CREATE OR REPLACE TABLE stg_orders AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_orders_dataset.csv', header = true, columns = {
    'order_id': 'VARCHAR', 'customer_id': 'VARCHAR', 'order_status': 'VARCHAR',
    'order_purchase_timestamp': 'TIMESTAMP', 'order_approved_at': 'TIMESTAMP',
    'order_delivered_carrier_date': 'TIMESTAMP', 'order_delivered_customer_date': 'TIMESTAMP',
    'order_estimated_delivery_date': 'TIMESTAMP'});

CREATE OR REPLACE TABLE stg_customers AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_customers_dataset.csv', header = true, columns = {
    'customer_id': 'VARCHAR', 'customer_unique_id': 'VARCHAR', 'customer_zip_code_prefix': 'INTEGER',
    'customer_city': 'VARCHAR', 'customer_state': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_order_items AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_order_items_dataset.csv', header = true, columns = {
    'order_id': 'VARCHAR', 'order_item_id': 'INTEGER', 'product_id': 'VARCHAR', 'seller_id': 'VARCHAR',
    'shipping_limit_date': 'TIMESTAMP', 'price': 'DOUBLE', 'freight_value': 'DOUBLE'});

CREATE OR REPLACE TABLE stg_reviews AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_order_reviews_dataset.csv', header = true, columns = {
    'review_id': 'VARCHAR', 'order_id': 'VARCHAR', 'review_score': 'INTEGER',
    'review_comment_title': 'VARCHAR', 'review_comment_message': 'VARCHAR',
    'review_creation_date': 'TIMESTAMP', 'review_answer_timestamp': 'TIMESTAMP'});

CREATE OR REPLACE TABLE stg_products AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_products_dataset.csv', header = true, columns = {
    'product_id': 'VARCHAR', 'product_category_name': 'VARCHAR', 'product_name_lenght': 'DOUBLE',
    'product_description_lenght': 'DOUBLE', 'product_photos_qty': 'DOUBLE', 'product_weight_g': 'DOUBLE',
    'product_length_cm': 'DOUBLE', 'product_height_cm': 'DOUBLE', 'product_width_cm': 'DOUBLE'});

CREATE OR REPLACE TABLE stg_sellers AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_sellers_dataset.csv', header = true, columns = {
    'seller_id': 'VARCHAR', 'seller_zip_code_prefix': 'INTEGER', 'seller_city': 'VARCHAR',
    'seller_state': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_geolocation AS
SELECT * FROM read_csv('{{RAW_DIR}}/olist_geolocation_dataset.csv', header = true, columns = {
    'geolocation_zip_code_prefix': 'INTEGER', 'geolocation_lat': 'DOUBLE', 'geolocation_lng': 'DOUBLE',
    'geolocation_city': 'VARCHAR', 'geolocation_state': 'VARCHAR'});

CREATE OR REPLACE TABLE stg_category_translation AS
SELECT * FROM read_csv('{{RAW_DIR}}/product_category_name_translation.csv', header = true, columns = {
    'product_category_name': 'VARCHAR', 'product_category_name_english': 'VARCHAR'});
