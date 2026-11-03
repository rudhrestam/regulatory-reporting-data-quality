"""
Regulatory Reporting Data Quality — demo.

A regulatory report is only as good as the file you submit. If a few hundred
records out of several thousand have a missing counterparty, a currency code
nobody recognises, a settlement date before the trade date, or an amount that
is not quantity x price, the whole submission can be questioned — and the
totals will not tie back to what the business expected to report.

This demo builds that problem on purpose, then runs the kind of
pre-submission data-quality framework that catches it:

  1. Generate ~5,000 synthetic financial trade records (the "clean" truth).
  2. Build a control file from the clean data: expected record count and
     expected total amount per report category.
  3. Plant realistic defects at known rates — missing fields, bad codes,
     out-of-range values, duplicate trade ids, and cross-field errors.
  4. Run named checks across six dimensions (schema, completeness, validity,
     uniqueness, consistency, reconciliation).
  5. Quarantine every record that fails anything, count the clean records as
     report-ready, and print a scorecard you can compare with the number of
     defects that were planted.

Everything is synthetic, the seed is fixed, and no real reporting data,
employer system, or employer name is involved anywhere.

Run:  python quality_demo.py
Writes: dq_scorecard.txt and quarantine.csv next to this file.
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7

# --- Generation configuration ---------------------------------------------
N_RECORDS = 5_000

INSTRUMENT_TYPES = ["STOCK", "BOND", "FX_SPOT", "SWAP", "FUTURE", "OPTION"]
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF"]
REPORT_CATEGORIES = ["EQUITIES", "FIXED_INCOME", "FX", "DERIVATIVES", "CASH"]
COUNTERPARTIES = [f"CP{i:03d}" for i in range(1, 41)]

REQUIRED_COLUMNS = [
    "trade_id", "account_id", "instrument_type", "trade_date",
    "settlement_date", "quantity", "price", "amount", "currency",
    "counterparty_code", "report_category",
]

# How many records get each defect. The slices below are disjoint on purpose:
# one planted problem per record (plus duplicate copies appended at the end),
# so the quarantine count can be checked directly against the planted total.
DEFECT_COUNTS = {
    "missing_required_field": 75,    # ~1.5% of records
    "invalid_code_or_currency": 60,  # ~1.2%
    "out_of_range_value": 50,        # ~1.0%
    "cross_field_inconsistency": 70,  # ~1.4%
    "duplicate_trade_id": 40,        # extra copies appended, not in the 5,000
}
AMOUNT_TOLERANCE = 0.02  # a cent or two of rounding is fine; anything more is not


# --- Clean data and the control file ---------------------------------------

def generate_clean(rng):
    """Generate the underlying trade records, before any defect is planted."""
    base_date = pd.Timestamp("2026-01-05")
    trade_dates = base_date + pd.to_timedelta(rng.integers(0, 60, N_RECORDS), unit="D")
    settlement_dates = trade_dates + pd.to_timedelta(
        rng.integers(1, 4, N_RECORDS), unit="D")

    quantity = rng.integers(10, 1_001, N_RECORDS)
    price = np.round(rng.uniform(1.0, 500.0, N_RECORDS), 2)

    df = pd.DataFrame({
        "trade_id": [f"T{100000 + i}" for i in range(N_RECORDS)],
        "account_id": [f"ACC-{n:04d}" for n in rng.integers(1000, 2000, N_RECORDS)],
        "instrument_type": rng.choice(INSTRUMENT_TYPES, N_RECORDS),
        "trade_date": trade_dates,
        "settlement_date": settlement_dates,
        "quantity": quantity,
        "price": price,
        "amount": np.round(quantity * price, 2),
        "currency": rng.choice(CURRENCIES, N_RECORDS),
        "counterparty_code": rng.choice(COUNTERPARTIES, N_RECORDS),
        "report_category": rng.choice(REPORT_CATEGORIES, N_RECORDS),
    })
    return df


def build_control_file(clean_df):
    """Expected count and total amount per category, from the clean data.

    This is what the submitted file is later reconciled against. In a real
    pipeline it would come from the source system, independently of the
    extract being checked — here it is computed before defects are planted,
    which preserves that independence.
    """
    control = (
        clean_df.groupby("report_category")
        .agg(expected_records=("trade_id", "size"),
             expected_amount=("amount", "sum"))
        .reindex(REPORT_CATEGORIES)
        .reset_index()
    )
    control["expected_amount"] = control["expected_amount"].round(2)
    return control


# --- Defect planting ---------------------------------------------------------

def plant_defects(df, rng):
    """Return a copy of the clean data with realistic defects planted in it."""
    df = df.copy()
    order = rng.permutation(N_RECORDS)
    n_missing = DEFECT_COUNTS["missing_required_field"]
    n_invalid = DEFECT_COUNTS["invalid_code_or_currency"]
    n_range = DEFECT_COUNTS["out_of_range_value"]
    n_cross = DEFECT_COUNTS["cross_field_inconsistency"]

    missing_idx = order[:n_missing]
    invalid_idx = order[n_missing:n_missing + n_invalid]
    range_idx = order[n_missing + n_invalid:n_missing + n_invalid + n_range]
    cross_idx = order[n_missing + n_invalid + n_range:
                      n_missing + n_invalid + n_range + n_cross]
    clean_idx = order[n_missing + n_invalid + n_range + n_cross:]

    # Missing required fields: rotate through three fields a report cannot
    # be submitted without.
    for i, idx in enumerate(missing_idx):
        field = ["account_id", "counterparty_code", "settlement_date"][i % 3]
        df.loc[idx, field] = np.nan

    # Invalid codes: a currency and an instrument nobody recognises, and
    # counterparty codes that do not follow the CP + three digits pattern.
    for i, idx in enumerate(invalid_idx):
        kind = i % 3
        if kind == 0:
            df.loc[idx, "currency"] = "XXX"
        elif kind == 1:
            df.loc[idx, "instrument_type"] = "CRYPTO"
        else:
            df.loc[idx, "counterparty_code"] = "BAD-CODE"

    # Out-of-range values: negative quantities and zero prices. The amount on
    # the record is left as generated, so these also break the
    # amount = quantity x price rule — the way a bad upstream value usually
    # shows up in more than one place.
    for i, idx in enumerate(range_idx):
        if i % 2 == 0:
            df.loc[idx, "quantity"] = -int(df.loc[idx, "quantity"])
        else:
            df.loc[idx, "price"] = 0.0

    # Cross-field inconsistencies: settlement before the trade date on half,
    # and an amount that is no longer quantity x price on the other half.
    for i, idx in enumerate(cross_idx):
        if i % 2 == 0:
            df.loc[idx, "settlement_date"] = (
                df.loc[idx, "trade_date"] - pd.Timedelta(days=2))
        else:
            df.loc[idx, "amount"] = round(float(df.loc[idx, "amount"]) * 1.30, 2)

    # Duplicate trade ids: exact copies of otherwise clean records, appended
    # at the end of the file the way a double-extracted batch would arrive.
    dup_source = rng.choice(clean_idx, DEFECT_COUNTS["duplicate_trade_id"],
                            replace=False)
    df = pd.concat([df, df.loc[dup_source]], ignore_index=True)
    return df.reset_index(drop=True)


# --- The data-quality framework ----------------------------------------------

def _failing(mask):
    """Positional indices where a boolean mask is True."""
    return set(np.flatnonzero(np.asarray(mask, dtype=bool)))


def run_checks(df, control):
    """Run every named check.

    Returns (results, failures) where results is a list of dicts, one per
    check, and failures maps a positional row index to the names of the
    record-level checks it failed. Reconciliation is a file-level check, so
    it appears in results but never quarantines a record by itself.
    """
    results = []
    failures = {}

    def record(name, dimension, failing_idx):
        results.append({
            "name": name, "dimension": dimension,
            "records_failed": len(failing_idx),
            "status": "PASS" if not failing_idx else "FAIL",
        })
        for idx in failing_idx:
            failures.setdefault(idx, []).append(name)

    trade_dates = pd.to_datetime(df["trade_date"], errors="coerce")
    settle_dates = pd.to_datetime(df["settlement_date"], errors="coerce")
    quantity = pd.to_numeric(df["quantity"], errors="coerce")
    price = pd.to_numeric(df["price"], errors="coerce")
    amount = pd.to_numeric(df["amount"], errors="coerce")

    # Schema — the columns are there, and the values in them parse.
    missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    results.append({
        "name": "schema_columns_present", "dimension": "schema",
        "records_failed": 0 if not missing_columns else len(df),
        "status": "PASS" if not missing_columns else "FAIL",
    })
    unparseable = (
        (trade_dates.isna() & df["trade_date"].notna())
        | (settle_dates.isna() & df["settlement_date"].notna())
        | (quantity.isna() & df["quantity"].notna())
        | (price.isna() & df["price"].notna())
        | (amount.isna() & df["amount"].notna())
    )
    record("schema_types_parseable", "schema", _failing(unparseable))

    # Completeness — every required field populated.
    record("completeness_required_fields", "completeness",
           _failing(df[REQUIRED_COLUMNS].isna().any(axis=1)))

    # Validity — allowed values, code formats, and sensible ranges. Nulls are
    # left to the completeness check so one missing value is counted once.
    record("validity_currency_allowed", "validity",
           _failing(df["currency"].notna() & ~df["currency"].isin(CURRENCIES)))
    record("validity_instrument_type_allowed", "validity",
           _failing(df["instrument_type"].notna()
                    & ~df["instrument_type"].isin(INSTRUMENT_TYPES)))
    record("validity_report_category_allowed", "validity",
           _failing(df["report_category"].notna()
                    & ~df["report_category"].isin(REPORT_CATEGORIES)))
    record("validity_counterparty_code_format", "validity",
           _failing(df["counterparty_code"].notna()
                    & ~df["counterparty_code"].astype(str)
                    .str.fullmatch(r"CP\d{3}", na=False)))
    record("validity_quantity_in_range", "validity",
           _failing(quantity.notna() & ((quantity <= 0) | (quantity > 100_000))))
    record("validity_price_in_range", "validity",
           _failing(price.notna() & ((price <= 0) | (price > 10_000))))

    # Uniqueness — the first occurrence of a trade id stands; later copies
    # are the duplicates and are the ones quarantined.
    record("uniqueness_trade_id", "uniqueness",
           _failing(df.duplicated(subset=["trade_id"], keep="first")))

    # Consistency — fields that have to agree with each other.
    record("consistency_settlement_not_before_trade", "consistency",
           _failing(trade_dates.notna() & settle_dates.notna()
                    & (settle_dates < trade_dates)))
    record("consistency_amount_equals_quantity_x_price", "consistency",
           _failing(quantity.notna() & price.notna() & amount.notna()
                    & ((amount - quantity * price).abs() > AMOUNT_TOLERANCE)))

    # Reconciliation — the incoming file against the control file, per
    # category. Detail rows are returned alongside the summary checks.
    actual = (
        df.groupby("report_category")
        .agg(actual_records=("trade_id", "size"), actual_amount=("amount", "sum"))
        .reindex(REPORT_CATEGORIES)
        .reset_index()
    )
    recon = control.merge(actual, on="report_category", how="left")
    recon["actual_records"] = recon["actual_records"].fillna(0).astype(int)
    recon["actual_amount"] = recon["actual_amount"].fillna(0.0).round(2)
    recon["count_delta"] = recon["actual_records"] - recon["expected_records"]
    recon["amount_delta"] = (recon["actual_amount"] - recon["expected_amount"]).round(2)

    count_mismatch = int((recon["count_delta"] != 0).sum())
    results.append({
        "name": "reconciliation_record_count_by_category",
        "dimension": "reconciliation",
        "records_failed": count_mismatch,
        "status": "PASS" if count_mismatch == 0 else "FAIL",
        "unit": "categories mismatched",
    })
    amount_mismatch = int((recon["amount_delta"].abs() > 0.01).sum())
    results.append({
        "name": "reconciliation_total_amount_by_category",
        "dimension": "reconciliation",
        "records_failed": amount_mismatch,
        "status": "PASS" if amount_mismatch == 0 else "FAIL",
        "unit": "categories mismatched",
    })
    return results, failures, recon


# --- Scorecard -----------------------------------------------------------------

def main():
    rng = np.random.default_rng(SEED)
    clean_df = generate_clean(rng)
    control = build_control_file(clean_df)
    incoming = plant_defects(clean_df, rng)

    results, failures, recon = run_checks(incoming, control)

    quarantined_idx = sorted(failures)
    quarantine = incoming.loc[quarantined_idx].copy()
    quarantine["failed_checks"] = [
        "; ".join(failures[idx]) for idx in quarantined_idx]
    report_ready = len(incoming) - len(quarantine)
    dq_score = report_ready / len(incoming) * 100
    planted_total = sum(DEFECT_COUNTS.values())
    checks_passed = sum(1 for r in results if r["status"] == "PASS")

    lines = []
    lines.append("=" * 72)
    lines.append("REGULATORY REPORTING — DATA QUALITY SCORECARD  (synthetic data)")
    lines.append("=" * 72)
    lines.append(f"Records generated (clean):    {N_RECORDS:,}")
    lines.append(f"Duplicate copies planted:     {DEFECT_COUNTS['duplicate_trade_id']:,}")
    lines.append(f"Records in the incoming file: {len(incoming):,}")
    lines.append("")
    lines.append("Defects planted (known, for comparison with what was caught):")
    for name, count in DEFECT_COUNTS.items():
        lines.append(f"  {name:<28}{count:>4}")
    lines.append(f"  {'TOTAL defective records':<28}{planted_total:>4}")
    lines.append("")
    lines.append(f"Checks run: {len(results)}   passed: {checks_passed}   "
                 f"failed: {len(results) - checks_passed}")
    lines.append("")
    lines.append(f"  {'Check':<46}{'Dimension':<16}{'Failed':>6}  Result")
    for r in results:
        unit = r.get("unit")
        failed = (f"{r['records_failed']} cat." if unit
                  else f"{r['records_failed']}")
        lines.append(f"  {r['name']:<46}{r['dimension']:<16}{failed:>6}  {r['status']}")
    lines.append("")
    lines.append("Reconciliation — incoming file vs control file (from clean data):")
    lines.append(f"  {'Category':<16}{'Exp. recs':>10}{'Act. recs':>10}{'dRecs':>7}"
                 f"{'Exp. amount':>16}{'Act. amount':>16}{'dAmount':>14}")
    for _, row in recon.iterrows():
        lines.append(
            f"  {row['report_category']:<16}{int(row['expected_records']):>10,}"
            f"{int(row['actual_records']):>10,}{int(row['count_delta']):>+7,}"
            f"{row['expected_amount']:>16,.2f}{row['actual_amount']:>16,.2f}"
            f"{row['amount_delta']:>+14,.2f}")
    total_count_delta = int(recon["count_delta"].sum())
    total_amount_delta = float(recon["amount_delta"].sum())
    lines.append(f"  {'TOTAL':<16}{int(recon['expected_records'].sum()):>10,}"
                 f"{int(recon['actual_records'].sum()):>10,}{total_count_delta:>+7,}"
                 f"{recon['expected_amount'].sum():>16,.2f}"
                 f"{recon['actual_amount'].sum():>16,.2f}"
                 f"{total_amount_delta:>+14,.2f}")
    lines.append("  Reconciliation result: FAIL — the incoming file does not tie to")
    lines.append("  the control file. The deltas are the duplicate copies and the")
    lines.append("  inflated amounts above; nothing should be submitted until the")
    lines.append("  quarantined records are fixed or excluded and the file re-ties.")
    lines.append("")
    lines.append(f"Records quarantined: {len(quarantine):,} "
                 f"(planted defective records: {planted_total:,})")
    lines.append(f"Records report-ready (clean): {report_ready:,}")
    lines.append(f"Overall data-quality score: {dq_score:.2f}% of records pass every check")
    lines.append("")
    lines.append("Sample of quarantined records (first 10):")
    for _, row in quarantine.head(10).iterrows():
        lines.append(f"  {row['trade_id']}  {row['report_category']:<14} "
                     f"failed: {row['failed_checks']}")
    lines.append("=" * 72)

    scorecard = "\n".join(lines)
    print(scorecard)

    here = Path(__file__).parent
    (here / "dq_scorecard.txt").write_text(scorecard + "\n", encoding="utf-8")
    quarantine.to_csv(here / "quarantine.csv", index=False)
    print(f"\nScorecard saved to dq_scorecard.txt; "
          f"{len(quarantine)} quarantined records written to quarantine.csv")


if __name__ == "__main__":
    main()
