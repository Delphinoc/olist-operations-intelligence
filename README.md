# Olist Operations Intelligence

E-commerce operations analytics on the public Olist Brazilian marketplace dataset: delivery reliability,
customer satisfaction and operational risk concentration. This README currently documents **Phase 1: the
analytical data model** and **Workstream 1 (delivery reliability)** and **Workstream 3 (customer satisfaction)**. See `reports/project_blueprint.md` for the full
project design, `reports/data_model_validation.md` for the validation of the model and
`reports/delivery_reliability_findings.md` and `reports/customer_satisfaction_findings.md` for the findings.

## Rebuild the model from the raw CSVs

Requirements: Python 3.10+ (developed on 3.14), packages in `requirements.txt` (`duckdb`, `pandas`, `numpy`, `matplotlib`, `scipy`, `statsmodels`, `pytest`).

```bash
pip install -r requirements.txt

# 1. download the dataset (see "Data source" below) and place the nine original Olist CSVs in data/raw/
#    (they are read-only inputs and are never modified)
# 2. build the DuckDB model (about a few seconds) -> data/processed/olist_model.duckdb
python scripts/build_model.py

# 3. run the automated tests (rebuilds into a temp database, compares with an independent pandas implementation)
python -m pytest

# 4. regenerate the validation report
python scripts/build_validation_report.py        # writes reports/data_model_validation.md

# 5. delivery-reliability workstream (tables, figures, stats JSON, then the findings report)
python analysis/delivery_reliability.py
python analysis/build_delivery_report.py         # writes reports/delivery_reliability_findings.md

# 6. customer-satisfaction workstream (needs statsmodels; about a minute)
python analysis/customer_satisfaction.py
python analysis/build_satisfaction_report.py     # writes reports/customer_satisfaction_findings.md
```

All paths are relative to the project root; no absolute paths are stored.
Optional arguments: `python scripts/build_model.py --raw <csv dir> --db <output .duckdb>`.

## Data source

The raw data is not included in this repository. Download "Brazilian E-Commerce Public Dataset by Olist"
from Kaggle: <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce> (requires a free Kaggle account),
unzip it and copy these nine files into `data/raw/`:

`olist_customers_dataset.csv`, `olist_geolocation_dataset.csv`, `olist_order_items_dataset.csv`,
`olist_order_payments_dataset.csv`, `olist_order_reviews_dataset.csv`, `olist_orders_dataset.csv`,
`olist_products_dataset.csv`, `olist_sellers_dataset.csv`, `product_category_name_translation.csv`.

Check the licence and terms on the Kaggle page before reusing or redistributing the data.

## Layout

| Path | Purpose |
|---|---|
| `data/raw/` | Original CSVs (git-ignored, read-only) |
| `data/processed/` | Built DuckDB database (git-ignored, regenerated) |
| `sql/01_staging.sql` | Typed copies of raw CSVs (`stg_*`) |
| `sql/02_dimensions.sql` | Parameters, `dim_date`, `dim_state` (IBGE macro-regions), `dim_zip_geo`, `dim_product`, `dim_seller` |
| `sql/03_facts.sql` | `fact_order_items`, `bridge_order_seller`, `fact_reviews`, `fact_orders`, `v_single_seller_orders` |
| `scripts/build_model.py` | Runs the SQL in order, then ~33 structural assertions (fails and removes the DB on violation) |
| `scripts/build_validation_report.py` | Runs the tests and writes the validation report |
| `scripts/profile_dataset.py`, `validate_kpis.py`, `build_*_report.py` | Earlier phases (profiling and KPI validation) |
| `sql/analysis/delivery/`, `sql/analysis/satisfaction/` | Primary KPI queries for the delivery-reliability and customer-satisfaction workstreams (DuckDB SQL) |
| `analysis/` | Python statistics, charts and findings-report generators (`delivery_reliability.py`, `customer_satisfaction.py`, `build_*_report.py`) |
| `tests/` | pytest suite, independent pandas reference (`reference_pandas.py`) and documented anchors (`anchors.py`) |
| `reports/` | Feasibility, KPI validation, blueprint, model validation and delivery-reliability reports; `tables/` (CSV) and `figures/` (PNG) hold analysis outputs |

## Key modelling rules (details in the blueprint)

- Window: purchases 2017-01 to 2018-08. Lateness: calendar date of delivery after the estimated date.
- `fact_orders` keeps **all** orders; populations are explicit flags (`is_delivery_kpi_eligible`, `is_seller_kpi_eligible`, `is_review_kpi_eligible`).
- Six mutually exclusive fulfilment classes; original `order_status` is preserved; cancelled/unavailable orders are never "open past promise".
- `review_score` exists only for orders with exactly one review row; no "latest review" is assumed.
- Seller attribution only for single-seller orders (`v_single_seller_orders`).
- Raw data is not committed. This project is not under version control yet and nothing has been pushed anywhere.
