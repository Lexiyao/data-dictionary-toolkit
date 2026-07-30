"""Express the frozen dictionary's constraints as SQL.

The pandas validator in `dictgen.validate` works on a table already in memory.
When the data lives in a database (or is too large to load), the same
constraints translate directly into SQL. This module generates that SQL from a
curated dictionary and runs it against SQLite, so the two paths can be compared
on the same data.

Constraints covered, mirroring `dictgen.validate`:
  required            NOT NULL / non-blank
  enum                value (mapped through value_map) IN (vocabulary)
  range               permitted_range bounds
  primary_key_unique  GROUP BY ... HAVING COUNT(*) > 1
"""
from __future__ import annotations

import sqlite3

import pandas as pd


def _sql_quote(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _canonical_case_expr(col: str, value_map: dict | None) -> str:
    """SQL that maps a raw value to its canonical form, mirroring
    `dictgen._to_canonical`: exact match in value_map first, then a
    case-folded match, else lower(trim(value))."""
    base = f"lower(trim(CAST({col} AS TEXT)))"
    if not value_map:
        return base
    whens = []
    for raw, canon in value_map.items():
        whens.append(f"WHEN trim(CAST({col} AS TEXT)) = {_sql_quote(raw)} "
                     f"THEN {_sql_quote(canon)}")
    for raw, canon in value_map.items():
        whens.append(f"WHEN {base} = {_sql_quote(str(raw).strip().lower())} "
                     f"THEN {_sql_quote(canon)}")
    return "CASE " + " ".join(whens) + f" ELSE {base} END"


def _allowed_terms(f: dict):
    """Only a pinned `enum_canonical` is enforceable — same rule as
    `dictgen._canonical_terms`. A profiler suggestion must never gate
    validation, or the two implementations would disagree."""
    terms = f.get("enum_canonical")
    return terms if terms else None


def build_checks(datapackage: dict, table: str = "data") -> list[dict]:
    """One dict per check: {check, field, sql}. Each query returns the
    offending rows/values, so an empty result means the constraint holds."""
    schema = datapackage["resources"][0]["schema"]
    checks = []

    for f in schema["fields"]:
        col = f'"{f["name"]}"'
        if f.get("required") is True:
            checks.append({
                "check": "required", "field": f["name"],
                "sql": f"SELECT COUNT(*) AS n FROM {table} "
                       f"WHERE {col} IS NULL OR trim(CAST({col} AS TEXT)) = ''",
            })
        terms = _allowed_terms(f)
        if terms is not None:
            canon = _canonical_case_expr(col, f.get("value_map"))
            vocab = ", ".join(_sql_quote(t) for t in terms)
            checks.append({
                "check": "enum", "field": f["name"],
                "sql": f"SELECT DISTINCT CAST({col} AS TEXT) AS bad_value FROM {table} "
                       f"WHERE {col} IS NOT NULL AND trim(CAST({col} AS TEXT)) <> '' "
                       f"AND ({canon}) NOT IN ({vocab})",
            })
        rng = f.get("allowed_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            lo, hi = rng
            checks.append({
                "check": "range", "field": f["name"],
                "sql": f"SELECT DISTINCT {col} AS bad_value FROM {table} "
                       f"WHERE {col} IS NOT NULL AND "
                       f"(CAST({col} AS REAL) < {lo} OR CAST({col} AS REAL) > {hi})",
            })

    for pk in schema.get("primaryKey", []):
        col = f'"{pk}"'
        checks.append({
            "check": "primary_key_unique", "field": pk,
            "sql": f"SELECT {col} AS bad_value, COUNT(*) AS n FROM {table} "
                   f"WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1",
        })
    return checks


def validate_sql(df: pd.DataFrame, datapackage: dict) -> pd.DataFrame:
    """Load `df` into an in-memory SQLite table and run the generated checks.
    Returns the same shape of report as `dictgen.validate`."""
    con = sqlite3.connect(":memory:")
    try:
        df.to_sql("data", con, index=False)
        rows = []
        for chk in build_checks(datapackage):
            res = pd.read_sql_query(chk["sql"], con)
            if res.empty:
                continue
            if chk["check"] == "required":
                n = int(res.iloc[0]["n"])
                if n:
                    rows.append({"severity": "error", "field": chk["field"],
                                 "check": "required", "detail": f"{n} missing value(s)"})
            elif chk["check"] == "primary_key_unique":
                dups = sorted(res["bad_value"].tolist())[:10]
                rows.append({"severity": "error", "field": chk["field"],
                             "check": "primary_key_unique",
                             "detail": f"duplicate keys: {dups}"})
            else:
                bad = sorted(str(v) for v in res["bad_value"].tolist())[:10]
                rows.append({"severity": "error", "field": chk["field"],
                             "check": chk["check"],
                             "detail": f"values outside "
                                       f"{'vocabulary' if chk['check'] == 'enum' else 'range'}: {bad}"})
        return pd.DataFrame(rows, columns=["severity", "field", "check", "detail"])
    finally:
        con.close()


if __name__ == "__main__":
    from pathlib import Path

    import yaml

    import dictgen as dg

    root = Path(__file__).resolve().parent
    curated = yaml.safe_load(open(root / "outputs" / "datapackage_curated.yaml"))
    batch = pd.read_csv(root / "data" / "sample_manifest_batch2.csv")

    print("=== generated SQL (first 3 checks) ===")
    for chk in build_checks(curated)[:3]:
        print(f"\n-- {chk['check']} on {chk['field']}\n{chk['sql']}")

    sql_report = validate_sql(batch, curated)
    pd_report = dg.validate(batch, curated)

    print("\n=== SQL validator ===")
    print(sql_report.to_string(index=False))
    print("\n=== pandas validator (same dictionary, same data) ===")
    print(pd_report[pd_report["severity"] == "error"].to_string(index=False))

    sql_keys = {(r.field, r.check) for r in sql_report.itertuples()}
    pd_keys = {(r.field, r.check) for r in pd_report[pd_report.severity == "error"].itertuples()}
    print(f"\nagreement: {'IDENTICAL' if sql_keys == pd_keys else 'DIFFER'}")
    if sql_keys != pd_keys:
        print("  sql-only:", sorted(sql_keys - pd_keys))
        print("  pandas-only:", sorted(pd_keys - sql_keys))
