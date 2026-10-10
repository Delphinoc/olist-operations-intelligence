# Final portfolio audit: Olist Operations Intelligence

Audit date: 2026-10-10. Scope: repository structure, dependencies, reproducibility, SQL/Python, tests, findings, documentation, Power BI preparation files and the saved `.pbix`. Nothing was committed or pushed. No existing code, report or test was deleted or changed (the only edits are listed in section 7).

## 1. Executive assessment

**Publication readiness: ready for GitHub after minor improvements.** The analytical core is strong: it has a validated model, an independent pandas reference, 185 passing tests, deterministic regeneration and consistently careful non-causal language. What remains is mostly packaging and Power BI verification:

- The `.pbix` is untracked, and several documents still say none exists.
- The measures inside the `.pbix` could not be compared with the documented DAX.
- There are no dashboard screenshots and no LICENSE.

**Strengths**
- Population flags, mutually exclusive fulfilment classes, explicit denominators and a "never add across levels" rule for overlapping segments.
- Findings are labelled Observed / Interpretation / Hypothesis. Seller instability (2 of 8 persist) and the review-timing dependence of the score gap are reported rather than hidden.
- Rebuilt outputs match committed outputs byte-for-byte (section 3).
- The repository holds no raw data, DuckDB files or credentials.

**Weaknesses**
- Dashboard evidence is limited to a binary file I could only partly inspect.
- Test counts embedded in four findings reports are point-in-time (60/84/113/158 vs 185 today).
- Only Python 3.14 was tested, and dependencies are unpinned.

## 2. Prioritized issue list

| # | Priority | Issue | Evidence | Recommended action |
|---|---|---|---|---|
| 1 | **High** | `.pbix` (9.8 MB) is untracked, but `powerbi/README.md`, `reports/powerbi_preparation_validation.md` and `reports/project_blueprint.md` say no `.pbix` exists. `tests/test_powerbi_export.py:348` asserts the phrase "No `.pbix` file" in `powerbi/README.md`. | `git status`; test source | Update those three documents *and* the test assertion together (needs your approval to change the test). I reverted my own attempt to edit the README so the suite stays green. |
| 2 | **High** | The `.pbix` contains measures and calculated columns that are **not in `powerbi/dax_measures.md`**, and the report is built on them. | Report definition extracted from the `.pbix` (section 4) | Add them to the dictionary, or rename them to match it, then run the reconciliation checklist in Desktop. |
| 3 | **High** | Derived row-level data (order and seller IDs, dates, scores) is embedded in the `.pbix`. The Olist licence terms were not checked here (I believe the Kaggle page lists CC BY-NC-SA 4.0, **unverified**). | `DataModel` stream is 9.8 MB | Check the Kaggle licence, then choose to commit the `.pbix` with attribution, keep it out of Git and publish screenshots only, or commit a snapshot-only version. Add attribution and a licence note to the README either way. |
| 4 | **High** | No dashboard screenshots exist, so the README can only link to analysis figures. | no image files in `powerbi/` | Export page images from Desktop to `docs/dashboard/` (or `powerbi/screenshots/`), then add them to the README Dashboard section. |
| 5 | **Medium** | `powerbi/dax_measures.md` has uncommitted edits (`ISCROSSFILTERED` note measure; `COUNTROWS = 1` guard on snapshot measures). The full suite passes with them in place. | `git diff` | Commit. Confirm the `.pbix` uses the guarded versions. |
| 6 | **Medium** | No `LICENSE` file for the code. | `ls` | Add one (for example MIT) and state that the data is under Olist's licence. |
| 7 | **Medium** | Python 3.10+ is documented but only 3.14 was tested. `requirements.txt` has lower bounds only, with no lock file. | `requirements.txt` | Add a tested-versions note (`pip freeze` into `requirements-lock.txt`) or test on 3.12. |
| 8 | **Medium** | Stale point-in-time test counts embedded in reports: 60 (data model, delivery), 84 (satisfaction), 113 (geographic), 158 (prioritization) vs 185 today. | `grep "passed in" reports/*.md` | Optionally regenerate with the `build_*_report.py` scripts. Regenerating *delivery* changes only the embedded test table (I tried and restored the file). The README no longer quotes them. |
| 9 | **Medium** | Review-timing caveat (page 3) and "screening policy, not statistical significance" (page 4) are required by `page_specs.md`. Page 4 has a screening/non-causal caveat textbox. I could not confirm the P0/P1 timing sentence on page 3, or the full policy wording on page 4. | textbox text extracted from the `.pbix` | Check manually in Desktop. |
| 10 | **Low** | `satisfaction_multi_review_rules.csv` differs from the committed copy in the 16th decimal (`...667` vs `...666`) when regenerated. | `git diff` | Cosmetic floating-point noise; ignore (I restored the file). |
| 11 | **Low** | The page 1 summary textbox in the `.pbix` carries number-format strings as its content and looks like a stray element. | extracted JSON | Check on the canvas. |
| 12 | **Low** | `.gitignore` had no Power BI temp patterns. | file review | Added `.pbi/`, `*.pbix.bak`, `*.pbi.tmp`, `~$*`, `AutoRecover/`. |
| 13 | **Low** | CRLF/LF warnings on commit. | git output | Optional `.gitattributes` with `* text=auto`. |

No credentials, API keys or absolute user paths were found in code, SQL, Markdown, JSON or Power Query files (pattern scan of the working tree, excluding `.git`). The saved `DataFolder` parameter value inside the `.pbix` cannot be inspected and may contain a local path.

## 3. Verified results (what was actually executed)

| Check | Result |
|---|---|
| `python -m pytest` (full suite, working tree with my edits) | **185 passed in 110.7 s** |
| `python -m pytest tests/test_powerbi_export.py` after README and `.gitignore` edits | 27 passed (one failure appeared when I edited the Power BI README wording, then passed after I reverted that edit) |
| `scripts/build_model.py` into a temp database from the local raw CSVs | 34/34 build assertions passed, about 4 s |
| `scripts/export_powerbi.py` into a temp folder | All 10 tables: row counts **and SHA-256 equal** to committed `manifest.json`; generated `power_query.m` identical to committed |
| Re-run of `analysis/delivery_reliability.py`, `customer_satisfaction.py`, `geographic_seller.py`, `operational_prioritization.py` | Tables, figures and stats JSON reproduce. Only one 16th-decimal float difference (issue 10). Delivery report generator also re-run. |
| Secret and absolute-path scan | none found |
| `.gitignore` effect | `data/raw/`, `data/processed/`, `*.duckdb`, `.pytest_cache/`, `__pycache__/`, `fact_orders.csv` and `fact_seller_orders.csv` are ignored. 199 tracked files, `.git` is 6.2 MB. |
| README internal links | all resolve (including this report) |

**Not executed:**
- A fresh download from Kaggle. The local `data/raw/` copies were used; their hashes were not compared with Kaggle's.
- The `build_validation_report.py` and `build_powerbi_validation_report.py` generators. They rewrite tracked reports and re-run the suite.
- The `build_satisfaction_report.py`, `build_geographic_seller_report.py` and `build_prioritization_report.py` generators. The underlying analysis scripts were run.
- Any Python version other than 3.14. Any non-Windows platform.

So: the rebuild path from the nine Olist CSVs to the model, Power BI package and analysis outputs is **verified on this machine**. Rebuilding from a freshly downloaded copy is **expected to work but was not tested**.

## 4. Power BI review

### 4a. What was verifiable from the saved `.pbix`

The `.pbix` is a zip. The report definition (`Report/definition`) is readable; the `DataModel` stream (tables, relationships, DAX, M queries) is compressed and **cannot be read outside Power BI Desktop**.

| Item | Finding |
|---|---|
| Pages | Four, in the documented order: 01 Executive Overview, 02 Delivery Performance, 03 Customer Experience, 04 Operational Priorities. All 1920x1080. Matches `page_specs.md`. |
| Relationship autodetection | Disabled in `Settings` (`IsRelationshipAutodetectionEnabled: false`), as the guide requires. |
| Visuals | Page 1: card, line, bar, table, 2 slicers. Page 2: 5 charts, card, 2 slicers. Page 3: 3 charts, card, 2 slicers. Page 4: scatter, column chart, table, card, shapes and textboxes. |
| Measures used by visuals (20) | In the dictionary under the same name: `Delivered orders`, `Late orders`, `Late-delivery rate`, `Severe-late rate`, `Median lead time (days)`, `P95 lead time (days)`, `Reviewed orders (P0)`, `Reviewed orders (P1)`, `Low-score orders (P0)`, `Low-score share (P0)`, `Low-score share (P1)`, `Review coverage (any review row)`, `Share of P0 reviews written before delivery`. |
| **Measures used but not in the dictionary (7)** | `Investigate States`, `Investigate Lanes`, `Investigate Sellers`, `Late Rate Minimum 100`, `Review Score Share`, `Seller Late Rate`, `Screening Threshold`. (The dictionary's equivalents are `Snapshot: Investigate segments`, `Late-delivery rate (n >= 100)`, `Seller: late-delivery rate` and `Snapshot: primary screening threshold`.) Their DAX **could not be checked**. |
| **Calculated columns used but not documented (6)** | `dim_date[Delay Period Group]`, `fact_orders[Delivery Status Label]`, `[Delivery Time Group]`, `[Review Star Label]`, `fact_seller_orders[Distance Range]`, `[Shipment Type]`. The README advises measures over calculated columns, and these add columns outside the exported package. |
| Columns referenced | All referenced physical columns (`days_late_band`, `distance_band`, `review_score`, `year_month`, `is_in_window`, snapshot fields) exist in the exported CSV schemas. |
| Snapshot table | Page 4 uses `snap_priority_candidates` fields only (`tier`, `excess_late`, `late_rate`, `late_rate_lo/hi`, `n_delivered`, `oe_s1`, `eligible`), consistent with the "no relationships, own slicers" rule. Whether page 4 has a date/state slicer synced to it could not be fully confirmed. |

### 4b. Documented definitions verified outside Power BI

- Documented relationships R1-R6: key uniqueness and foreign-key matching are enforced by the tests (`test_powerbi_export.py`), and the exported CSVs reproduce the checksums. The relationship table in `powerbi/README.md` is consistent with `manifest.json` and with the test lint.
- Documented DAX: evaluated through Python equivalents on the exported CSVs across filter-context scenarios and compared with independent SQL (27 tests, all passing). This validates the *logic*, **not** that the DAX text runs and returns the same figures in Power BI. The DAX text itself was checked for `DIVIDE` use, no delivery-date filters and the Wilson-expression guards.
- Source data paths: `power_query.m` uses a `DataFolder` parameter plus relative file names, with no absolute paths in the repository.

### 4c. Manual Power BI Desktop checks still required

1. Open the `.pbix` and confirm the model view shows exactly six relationships R1-R6, one-to-many, single direction, none touching the two fact tables together or any `snap_*` table.
2. Compare each of the 7 undocumented measures and 6 calculated columns against the dictionary. Document or rename them.
3. Work through checklist sections 9A-D in `powerbi/README.md` (anchors 99,441 / 99,092 / 96,203 / 6,531 / 2,860 / 94,931 / 95,037 / 90,103; scenario checks RJ, Northeast, Nov 2017-Mar 2018, SP>RJ; snapshot counts 10 / 9 / 8 and threshold 20).
4. Confirm that changing date, state or seller slicers does not change any snapshot figure on page 4.
5. Confirm the page 3 review-timing sentence and the page 4 "screening policy, not statistical significance" wording are on the canvas.
6. Confirm `dim_date` is marked as the date table and that numeric columns are set to *Don't summarize*.
7. Check the `DataFolder` parameter. A published copy should not carry a personal absolute path.
8. Export screenshots of the four pages.

## 5. Scorecard (qualitative, not a measure of hiring likelihood)

| Category | Score | Evidence |
|---|---:|---|
| Business relevance | 4 | Clear operations question, defined stakeholders, recommendations framed as investigation priorities. No cost data, so no impact sizing, which is the honest choice. |
| Technical implementation | 5 | SQL-built DuckDB model, build-time assertions, independent pandas reference, Wilson/bootstrap/standardisation/clustered regression, 185 tests. |
| Analytical correctness | 4 | Populations, denominators and overlaps handled carefully, and sensitivities reported. Held back by the unverified in-`.pbix` DAX. |
| Reproducibility | 4 | Verified rebuild with identical checksums. Only Python 3.14 tested, no lock file, Kaggle download step manual. |
| Documentation quality | 4 | Detailed reports and a recruiter-oriented README now. Some stale statements remain (issue 1, 8). |
| Visual presentation | 3 | Many clean analysis figures, but no dashboard screenshots and the `.pbix` was only partly reviewable. |
| Interview readiness | 4 | Strong story and many defensible design decisions. The dashboard measure inventory must be reconciled first. |

## 6. Reproducibility checklist

| Item | Status |
|---|---|
| Dependencies documented (`requirements.txt`) | Verified present, lower bounds only |
| Python version stated | Stated as 3.10+; **only 3.14 verified** |
| Dataset access instructions | Documented. Not re-downloaded. |
| Relative paths | Verified: model, export, tests and analysis ran from the repo root |
| Seeds | Seeded bootstrap (seed documented); regeneration reproduced results |
| Model build, tests, export | Executed and verified |
| Analysis regeneration | Executed and verified (4 workstreams) |
| Dashboard rebuild from the package | **Not verifiable without Power BI Desktop** |

## 7. Changes made in this audit

- `README.md` rewritten for recruiters (previous content remains in Git history). It leads with the business problem, verified findings (split into observed rates, single-seller analysis, P0/P1 sensitivity and fixed-period snapshot), recommendations, dashboard, methods, stack, structure, data source, reproduction steps, testing and limitations. No screenshot is embedded for the dashboard because none exists; only existing analysis figures are linked.
- `.gitignore`: added Power BI temporary-file patterns.
- `reports/final_portfolio_audit.md`: this file.
- Not changed: scripts, tests, SQL, findings reports, `powerbi/*` documentation and `dax_measures.md` (which still carries your uncommitted edits).
- Re-running generators briefly rewrote `reports/delivery_reliability_findings.md` and one table. Both were restored with `git checkout` and are clean.

## 8. Interview preparation

1. **Why is the late rate "conditional", and what would change it?** Be ready to explain the delivered-only denominator, the six fulfilment classes and the cohort-maturity sensitivity.
2. **Why does the review-score gap shrink from 53 to 16 points in P1?** Review timing: 74% of late-order reviews were written before delivery. Be ready to explain what this does and does not say about causation.
3. **How did you decide which segments to "Investigate"?** Minimum volume, interval above the reference, 20-order policy (not significance), window-half consistency. Be ready to discuss the multiplicity caveat and why sellers are secondary evidence (2 of 8 persist).
4. **Why single-seller orders only, and why are state, lane and seller excess not additive?** Attribution and double counting (+2,658 vs +1,384).
5. **How did you validate the dashboard?** Python DAX equivalents vs independent SQL, and what remains unverified until the checklist is run in Desktop.

## 9. Proposed Git commits (not executed; awaiting your approval)

Current state: `powerbi/dax_measures.md` modified, `README.md` and `.gitignore` modified, `.pbix` and this report untracked.

1. `Refine Power BI DAX dictionary: single-segment snapshot guards and ISCROSSFILTERED note` (`powerbi/dax_measures.md`)
2. `Ignore Power BI temporary files` (`.gitignore`)
3. `Rewrite README for publication: findings, recommendations, reproduction` (`README.md`)
4. `Add final portfolio audit` (`reports/final_portfolio_audit.md`)
5. After you decide on issues 1-3: `Add Power BI report (.pbix) and reconcile documentation` (the `.pbix`, the stale "no .pbix" statements, test assertion, undocumented measures)
6. Optional: `Add LICENSE and dashboard screenshots`

Commits 1-4 are low risk. Hold commit 5 until the Desktop checks and the licence decision are done. I would not push until then.

---

## 10. Addendum: final publication cleanup (2026-10-10)

| Audit issue | Status after cleanup |
|---|---|
| 1. Stale "no .pbix" statements and the test pinning them | **Resolved.** `powerbi/README.md`, `reports/powerbi_preparation_validation.md` and `reports/project_blueprint.md` now acknowledge the four-page `.pbix` and keep the Stage A text as history. The test assertion `"No .pbix file" in readme` was replaced by `test_documentation_acknowledges_the_saved_pbix_report` (checks the acknowledgement, preserved history, that all 13 dashboard-only objects are listed and unverified, and that none has an invented DAX definition). No other test was changed. |
| 2. Dashboard-only measures and columns | **Resolved for documentation (see 10a).** At first the 7 measures and 6 calculated columns were listed without formulas, because the `.pbix` data model is compressed. They are now documented with exact formulas in `powerbi/dax_measures.md` section 8, from a Tabular Editor 2 export of the saved model (`powerbi/dax_export.csv`). Execution in Power BI Desktop is still not verified. |
| 3. Dataset licence | **Verified** as CC BY-NC-SA 4.0 from Kaggle's dataset metadata (owner Olist, title "Brazilian E-Commerce Public Dataset by Olist") and the Kaggle listing. README now carries attribution and separates dataset licence from code licence. |
| 4. Screenshots | **Still open.** A PDF export of the four pages now exists in `powerbi/visualisation/`. Its text values agree with the verified findings (96,203 delivered; 6.79% late; 2.97% severe; 95,037 single-review orders; P0 9.2% / 62.4% and P1 9.2% / 25.0% low-score shares; 10 / 9 / 8 Investigate; threshold 20; high-delay months 15.1% vs 4.5%). No images were generated; the README states that screenshots are pending. |
| 5. DAX edits uncommitted | Unchanged (still to be committed). |
| 6. LICENSE | **Not created; approval needed** (below). |

**Dashboard wording to review in Desktop (from the PDF text, not a verified defect):** page 4 recommends investigating "carrier capacity and promotional demand" and "carrier allocation, transit time and last-mile delivery". The data contains no carrier or promotion information, so these read as hypotheses to investigate. Consider prefixing them with "Hypotheses to investigate:" to match the project's non-causal wording.

**Licence proposal (not applied).** For original code (SQL, Python, tests, documentation) I propose the **MIT License**: short, permissive, widely understood by hiring managers. Scope: all original files in this repository except the Olist-derived tables in `reports/tables/`, the figures, `powerbi/data/*.csv` and `reports/*.json`, which stay under CC BY-NC-SA 4.0 as aggregates derived from the dataset. To create it I need the copyright holder's name as it should appear (Git shows the author as "Delphinoc"; I did not assume a legal name) and your confirmation of MIT versus another licence. Choosing a licence is your decision, and a permissive licence cannot be withdrawn from copies already distributed.

**Safe `.gitignore` state.** `*.pbix`, `*.pbit`, `*.abf`, `data/raw/`, `data/processed/`, `*.duckdb`, `powerbi/data/fact_orders.csv`, `powerbi/data/fact_seller_orders.csv`, credentials patterns and caches are ignored (verified with `git check-ignore`). No local file was deleted. The aggregate snapshot tables and dimension CSVs in `powerbi/data/` remain tracked as before (they contain no review text, customer identifiers or raw timestamps, per the Stage A tests; seller IDs are Olist's pseudonymous hashes).

**`.pbip` feasibility.** Documented in `powerbi/README.md` section 11 as an owner-run workflow with explicit data checks. No `.pbip` was created or inspected, so none is claimed to be data-free.

**Test run after cleanup:** `python -m pytest`: **186 passed in 114.5 s** (185 previous + 1 new documentation check).

### 10a. Update: dashboard-only DAX formulas documented (2026-10-10)

- **Formulas:** all 13 dashboard-only objects (7 measures, 6 calculated columns) are documented with their exact expressions in `powerbi/dax_measures.md` section 8. Source: `powerbi/dax_export.csv`, exported from the saved model with Tabular Editor 2 (as reported by the report owner). `test_dashboard_only_objects_match_export_and_reconcile` compares the documented text with that file when it is present.
- **What was reconciled, and where:** the expressions were evaluated in Python on the exported package and compared with independent SQL on the DuckDB model and the validated report tables (all matched, section 8.4). This is **SQL/Python reconciliation only**. The formulas have **not been executed in Power BI Desktop by this project**, and the values they return there are still a manual check (`powerbi/README.md`, section 9).
- **Single-seller population:** the page 2 shipment-type and shipping-distance charts use `Seller Late Rate` on the 94,931 single-seller orders (6.87% late), not the 96,203 delivered orders (6.79%). Now stated in `README.md` and `powerbi/README.md`.
- **Lead-time bins:** `Delivery Time Group` uses half-open intervals on fractional days ("0–4 days" = under 5.0 days). Now stated in both READMEs.
- **Export file hygiene:** `powerbi/dax_export.csv` (2,999 bytes) was inspected: no absolute paths, user names, e-mail addresses, URLs, connection strings, secrets or ID-like values. The only match for "key" is the column reference `snap_metadata[key]`. It is not ignored by `.gitignore` and is ready to be tracked once you approve.
- **Flags F1-F8 in section 8.5 are unchanged and remain open for your decision.** No expression, KPI definition or the `.pbix` was modified.
