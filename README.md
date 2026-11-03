# Regulatory Reporting Data Quality (Demo)

A regulatory report does not usually fail because the model was wrong. It fails because the file was: a counterparty code missing here, a currency nobody recognises there, a settlement date that lands before the trade date, a few records extracted twice — and totals that no longer tie back to what the business expected to report. By the time someone notices, the submission clock is already running. This project builds a small version of that problem on purpose, then runs the pre-submission data-quality framework that catches it before anything goes out.

> This is a personal portfolio project on data-quality and reconciliation patterns for financial reporting. It is built on 100% synthetic data with original code, and does not represent work done for any employer. No employer names, systems, or real reporting data appear anywhere in it.

## Why reconciliation needs a control file

Checking records one by one is not enough. A file can pass every field-level rule and still be wrong — a batch extracted twice, for instance, contains only perfectly valid records, just too many of them. So alongside the record checks, the demo builds a **control file** first: the expected record count and expected total amount per report category, computed from the clean underlying data *before* any defects are planted. That independence is the point. The incoming file is then reconciled against the control totals, category by category, and any delta has to be explained before the file is reportable.

## What the demo does

`quality_demo.py` generates 5,000 synthetic trade records (trade id, account, instrument type, trade and settlement dates, quantity, price, amount, currency, counterparty code, and report category), builds the control file from them, and then plants defects at known rates:

| Planted defect | Count | What it looks like |
|---|---:|---|
| Missing required fields | 75 | Account, counterparty code, or settlement date left blank |
| Invalid codes / currencies | 60 | An unrecognised currency or instrument, a counterparty code in the wrong format |
| Out-of-range values | 50 | Negative quantities, zero prices |
| Cross-field inconsistencies | 70 | Settlement before trade date; amount ≠ quantity × price |
| Duplicate trade ids | 40 | Exact copies of clean records appended, like a double-extracted batch |
| **Total defective records** | **295** | in a 5,040-record incoming file |

It then runs 14 named checks across six dimensions:

- **Schema** — required columns present; values parse as the right types
- **Completeness** — every required field populated
- **Validity** — currencies, instrument types, and report categories from allowed lists; counterparty codes matching the expected format; quantity and price in range
- **Uniqueness** — no trade id appearing twice (the first copy stands, later copies are quarantined)
- **Consistency** — settlement date not before trade date; amount equals quantity × price within a small rounding tolerance
- **Reconciliation** — record counts and total amounts per category against the control file

Every record that fails anything is quarantined — written to `quarantine.csv` with the names of the checks it failed — and the rest are counted as report-ready. Nothing bad is silently dropped, and nothing bad passes through.

## Example output

Actual output from a run (fixed seed 7, so you'll get the same numbers):

```
Checks run: 14   passed: 3   failed: 11

  Check                                         Dimension       Failed  Result
  schema_columns_present                        schema               0  PASS
  schema_types_parseable                        schema               0  PASS
  completeness_required_fields                  completeness        75  FAIL
  validity_currency_allowed                     validity            20  FAIL
  validity_instrument_type_allowed              validity            20  FAIL
  validity_report_category_allowed              validity             0  PASS
  validity_counterparty_code_format             validity            20  FAIL
  validity_quantity_in_range                    validity            25  FAIL
  validity_price_in_range                       validity            25  FAIL
  uniqueness_trade_id                           uniqueness          40  FAIL
  consistency_settlement_not_before_trade       consistency         35  FAIL
  consistency_amount_equals_quantity_x_price    consistency         85  FAIL
  reconciliation_record_count_by_category       reconciliation  5 cat.  FAIL
  reconciliation_total_amount_by_category       reconciliation  5 cat.  FAIL

Reconciliation — incoming file vs control file (from clean data):
  TOTAL    expected 5,000 records / $639,890,642.51
           actual   5,040 records / $645,839,263.36
           delta       +40 records / +$5,948,620.85   -> FAIL

Records quarantined: 295 (planted defective records: 295)
Records report-ready (clean): 4,745
Overall data-quality score: 94.15% of records pass every check
```

Two things worth noticing. First, the quarantine count lands exactly on the planted count — 295 out, 295 in — so the framework is neither missing defects nor throwing out good records. Second, the amount-consistency check fails 85 records, not just the 35 whose amounts were tampered with: the 50 out-of-range records (negative quantity, zero price) also break amount = quantity × price, because that is how a bad upstream value normally behaves — it shows up in more than one place. And the reconciliation failure is just as informative: the +40 count delta is the duplicated batch, and the amount delta is the duplicates plus the inflated amounts, itemised per category in the full scorecard.

## Run it

```bash
pip install -r requirements.txt
python quality_demo.py
```

Prints the DQ scorecard, saves a copy to `dq_scorecard.txt`, and writes the quarantined records (with their failed checks) to `quarantine.csv`.

## Tech stack

Python · NumPy · pandas

## Ideas for extension

- Split the scorecard by report category or counterparty, so a failing category can be held back while clean categories are submitted on time.
- Add timeliness and drift checks: file arrival against a submission calendar, and per-field distributions compared with the previous period to catch a feed that changed silently rather than broke loudly.
- Feed the quarantine output into a repair-and-recheck loop — fix a record, re-run only its failed checks, and release it back into the report-ready set with an audit trail.
- Swap the synthetic generator for a real extract schema (the checks are written against column names and allowed-value lists, so only the configuration at the top needs to change).
