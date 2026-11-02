"""Generate machine-readable + human-readable data dictionaries from a table,
and validate later batches against a frozen dictionary.

Field-level profiling only: types, domains, missingness, key candidates,
plus mechanical hygiene flags (case/whitespace variants, mixed date formats,
out-of-range numerics). No inference about scientific meaning -- the
`definition` and `unit` slots are left for a human to fill.

Curation model
--------------
The profiler proposes; only a human disposes. Every constraint that
`validate()` enforces lives in a *curated* slot, and the profiler never writes
to those slots:

    profiler-owned (refreshed on every run)   curated (enforced, never touched)
    -----------------------------------       ---------------------------------
    logical_type, storage_dtype               definition, unit
    n_rows, n_missing, n_unique               required
    example_values, observed_range            permitted range (`allowed_range`)
    distinct_values, enum_observed            `enum_canonical` + `value_map`
    enum_canonical_suggested (ADVISORY)       controlled_vocabulary
    profiled_complete, issues, role_candidate (plus any slot a steward adds)

Three rules follow, each of which was a bug before it was a rule:

1. **A suggestion never gates validation.** `enum_canonical_suggested` is
   advisory output. Only `enum_canonical`, pinned by a steward, is enforced.
   Otherwise re-profiling silently widens the vocabulary to admit whatever the
   new batch happens to contain -- the dictionary stops being frozen.
2. **`required` is a policy assertion**, not an artefact of whichever extract
   was profiled. The profiler records `profiled_complete`; the steward decides.
3. **A vocabulary needs an explicit raw->canonical map.** `M`, `1` and `male`
   are one concept; only a human can say so. `validate()` maps values through
   `value_map` before testing membership, so the source table validates against
   its own dictionary.

`merge_curated()` refreshes profiled statistics from new data while preserving
every curated slot -- including a steward's *deletion* of one, since only
curated keys are enforced.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

import numpy as np
import pandas as pd

# --- inferred logical types -------------------------------------------------
ENUM_MAX_CARD = 25          # <= this many distinct values -> candidate enum
ENUM_MAX_RATIO = 0.20       # ...and distinct/rows below this
ID_MIN_UNIQUE_RATIO = 0.95  # >= this unique -> candidate identifier
DISTINCT_RECORD_MAX = 50    # record the full value set up to this cardinality
CONSTANT_FLAG_MIN_COVERAGE = 0.5  # only call a column "constant" if this populated

DRAFT_VERSION = "0.1.0-draft"

# Strings that mean "absent" rather than being data. Excluded from proposed
# vocabularies so a sentinel cannot be frozen into a controlled vocabulary.
MISSING_SENTINELS = frozenset(
    {"NA", "N/A", "na", "n/a", "NULL", "null", "None", "none", "-", ".", "?",
     "unknown", "UNKNOWN", "Unknown", "missing", "MISSING"}
)

# Slots the profiler owns and always refreshes from the current data. Every
# other slot on a field is treated as human-authored (curated) and preserved
# on re-run by `merge_curated`. `enum_canonical_suggested` is here because it
# is profiler output -- and precisely because it is refreshed, it must never be
# enforced by `validate`.
PROFILED_KEYS = frozenset({
    "name", "logical_type", "storage_dtype", "n_rows", "n_missing", "n_unique",
    "example_values", "observed_range", "enum_observed", "distinct_values",
    "enum_canonical_suggested", "value_map_suggested", "issues",
    "role_candidate", "profiled_complete",
})

_DATE_PATTERNS = [
    (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),
    (r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d"),
    (r"^\d{2}/\d{2}/\d{4}$", "%d/%m/%Y or %m/%d/%Y (ambiguous)"),
    (r"^\d{2}-\d{2}-\d{4}$", "%d-%m-%Y or %m-%d-%Y (ambiguous)"),
    (r"^[A-Za-z]{3}\s+\d{1,2},?\s+\d{4}$", "%b %d %Y"),
]
_DATE_RE = [(re.compile(p), label) for p, label in _DATE_PATTERNS]


def _date_formats(values: pd.Series) -> list[str]:
    """Date formats present in a string column, matched vectorised per pattern."""
    if not len(values):
        return []
    text = values.astype(str).str.strip()
    return [label for rx, label in _DATE_RE if text.str.match(rx).any()]


def _plain(v):
    """A YAML/JSON-safe plain Python value. numpy scalars, pandas Timestamps
    and NaT all leak into rendered artifacts otherwise (`np.float64(217.0)`)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.isoformat()
    if isinstance(v, np.generic):
        return v.item()
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    return v


def _plain_list(values) -> list:
    return [_plain(v) for v in values]


def _norm_token(value) -> str:
    """Render any value as the canonical string token used for vocabulary
    comparison. Numeric codes are the trap here: a `sex` column coded 1/2 is
    int64 in a complete batch and float64 the moment one row is missing, so
    `str()` alone yields '1' then '1.0' and every row falls out of vocabulary.
    Integral floats are therefore rendered without the decimal tail, and
    booleans are lower-cased so a 0/1 database export still compares equal."""
    v = _plain(value)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _to_canonical(value, value_map: dict | None, logical_type: str | None = None) -> str:
    """Map a raw value to its canonical form: exact match in `value_map`
    (steward-declared synonyms) first, then a case-folded match, else a plain
    case/whitespace fold. Map keys are normalised the same way as values, so an
    unquoted YAML key (`1:` rather than `'1':`) or one with stray whitespace
    still resolves.

    For a field declared `boolean`, 0/1 are read as false/true. SQL has no
    boolean type, so a round-trip through SQLite or a CSV export turns a
    boolean column into 0/1; that is a storage artefact, not a new vocabulary.
    """
    token = _norm_token(value)
    if logical_type == "boolean" and token in ("0", "1"):
        token = "true" if token == "1" else "false"
    if value_map:
        for raw_key, canon in value_map.items():
            if _norm_token(raw_key) == token:
                return _norm_token(canon)
        lowered = token.lower()
        for raw_key, canon in value_map.items():
            if _norm_token(raw_key).lower() == lowered:
                return _norm_token(canon)
    return token.lower()


def _is_text(s: pd.Series) -> bool:
    """Whether a column holds text, whatever dtype pandas stores it in: object,
    any pandas string dtype, or a categorical whose categories are strings.

    pandas 2 reads text as `object`; pandas 3 reads it as `str`. The blank-cell
    filter, the prose test and every hygiene flag in the text block (whitespace,
    case variants, mixed date formats, zero-padded codes, sentinels) used to be
    gated on `s.dtype == object`, so under pandas 3 none of them ran and the text
    findings silently vanished. A check that did not run must not look like a
    check that passed.
    """
    return pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)


def _logical_type(s: pd.Series, nonnull: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if len(nonnull) and _date_formats(nonnull):
        text = nonnull.astype(str).str.strip()
        matched = np.zeros(len(text), dtype=bool)
        for rx, _ in _DATE_RE:
            matched |= text.str.match(rx).to_numpy(dtype=bool)
        if matched.mean() > 0.8:
            return "date-like string"
    return "string"


def _hygiene_flags(s: pd.Series, nonnull: pd.Series, n_rows: int) -> list[str]:
    """Mechanical observations about one column. `nonnull` has already had
    NULLs and blank strings removed; blanks are recounted from `s` so the
    empty-string check actually fires."""
    flags = []
    n_null = int(s.isna().sum())
    n_blank = 0
    if _is_text(s):
        text = s.dropna().astype(str).str.strip()
        n_blank = int((text == "").sum())
        if n_blank:
            flags.append(f"{n_blank} empty-string cell(s) (distinct from NULL -- unify)")
    n_absent = n_null + n_blank
    if n_absent == n_rows and n_rows:
        flags.append("column entirely empty -- drop or document why it exists")
    elif n_absent:
        flags.append(f"{n_absent}/{n_rows} missing ({n_absent / n_rows:.1%})")

    coverage = len(nonnull) / max(n_rows, 1)
    if len(nonnull) and nonnull.nunique(dropna=True) == 1 and coverage >= CONSTANT_FLAG_MIN_COVERAGE:
        flags.append("single constant value -- belongs in dataset-level metadata, not a column")

    if _is_text(s) and len(nonnull):
        vals = nonnull.astype(str)
        if (vals != vals.str.strip()).any():
            flags.append("leading/trailing whitespace present")
        norm = vals.str.strip().str.lower()
        collapsed = norm.nunique()
        if collapsed < vals.nunique():
            groups: dict[str, set] = {}
            for raw, key in zip(vals, norm):
                groups.setdefault(key, set()).add(raw)
            examples = [sorted(v) for v in groups.values() if len(v) > 1][:3]
            flags.append(
                f"case/whitespace variants collapse {vals.nunique()} -> {collapsed} values, "
                f"e.g. {examples}"
            )
        fmts = _date_formats(nonnull)
        if len(fmts) > 1:
            flags.append(f"mixed date formats: {fmts}")
        if vals.str.fullmatch(r"0\d+").any():
            flags.append("zero-padded numeric-looking codes -- read as string or leading zeros are lost")
        sentinels = sorted(set(vals.str.strip()) & MISSING_SENTINELS)
        if sentinels:
            flags.append(f"missing-value sentinels used as data: {sentinels} -- convert to NULL")

    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and len(nonnull) > 3:
        q1, q3 = np.percentile(nonnull.astype(float), [25, 75])
        iqr = q3 - q1
        lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
        far = nonnull[(nonnull < lo) | (nonnull > hi)]
        if len(far):
            flags.append(
                f"{len(far)} value(s) beyond 3xIQR fence [{lo:.3g}, {hi:.3g}]: "
                f"{_plain_list(sorted(far.unique())[:5])} -- confirm plausible range"
            )
    return flags


def profile_table(df: pd.DataFrame) -> list[dict]:
    """One profile dict per column. Proposes; never enforces."""
    n_rows = len(df)
    out = []
    for name in df.columns:
        s = df[name]
        nonnull = s.dropna()
        if _is_text(s):
            nonnull = nonnull[nonnull.astype(str).str.strip() != ""]
        n_unique = int(nonnull.nunique())
        rec: dict = {
            "name": name,
            "definition": "TODO: one sentence, in the units the instrument reports",
            "unit": None,
            "logical_type": _logical_type(s, nonnull),
            "storage_dtype": str(s.dtype),
            "n_rows": n_rows,
            "n_missing": int(n_rows - len(nonnull)),
            "n_unique": n_unique,
            "example_values": _plain_list(nonnull.unique()[:4]),
        }
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s) and len(nonnull):
            rec["observed_range"] = [float(nonnull.min()), float(nonnull.max())]
            rec["allowed_range"] = "TODO: physically/biologically valid min,max"

        # Record the full value set whenever it is small enough to enumerate,
        # independently of enum eligibility. `merge_curated` needs this to spot
        # values a curated vocabulary does not cover, including for columns that
        # have stopped looking like enums.
        if n_unique and n_unique <= DISTINCT_RECORD_MAX:
            rec["distinct_values"] = _plain_list(sorted(nonnull.unique(), key=_norm_token))

        # An enum proposal needs low cardinality AND must not be a date, a
        # mostly-absent free-text field, prose, or a numeric/boolean column
        # (those are quantities and flags, not vocabularies -- and their
        # rendering shifts with dtype, which silently invalidates a frozen set).
        looks_prose = False
        if _is_text(s) and len(nonnull):
            vals = nonnull.astype(str)
            looks_prose = bool(
                (vals.str.len().median() > 25)
                or (vals.str.contains(r"[;:.,]").mean() > 0.5)
            )
        enum_eligible = (
            n_unique
            and n_unique <= ENUM_MAX_CARD
            and n_unique / max(n_rows, 1) <= ENUM_MAX_RATIO
            and rec["logical_type"] not in ("date", "date-like string", "boolean",
                                            "integer", "number")
            and not looks_prose
            and (len(nonnull) / max(n_rows, 1)) >= 0.5
        )
        if enum_eligible:
            raw = sorted((str(v) for v in nonnull.unique()), key=str)
            # A sentinel means "absent"; it must never be frozen as a term.
            terms = [r for r in raw if r.strip() not in MISSING_SENTINELS]
            rec["enum_observed"] = raw
            rec["enum_canonical_suggested"] = sorted({r.strip().lower() for r in terms})
            # Seeded raw->canonical map, keyed on the *stripped* raw value so
            # the key is reachable by lookup. Case/whitespace folding only; the
            # steward records semantic merges ("M" -> "male") and pins the
            # allowed set in `enum_canonical`, which is what validation uses.
            rec["value_map_suggested"] = {r.strip(): r.strip().lower() for r in terms}
            rec["controlled_vocabulary"] = "TODO: ontology (e.g. UBERON/MONDO/PATO) + term IDs per value"

        if n_unique and n_unique / max(n_rows, 1) >= ID_MIN_UNIQUE_RATIO:
            rec["role_candidate"] = "identifier"
        # `required` is a policy assertion the steward sets during curation, NOT
        # an artefact of whether this extract was complete. Record the
        # observation only; leave the constraint unset until a human declares it.
        rec["profiled_complete"] = rec["n_missing"] == 0
        rec["required"] = None
        rec["issues"] = _hygiene_flags(s, nonnull, n_rows)
        out.append(rec)
    return out


def key_candidates(df: pd.DataFrame, fields: list[dict]) -> dict:
    """Single-column primary-key candidates and their duplicate offenders."""
    res: dict = {"primary_key_candidates": [], "duplicated_in": {}}
    for f in fields:
        if f.get("role_candidate") != "identifier":
            continue
        s = df[f["name"]]
        dups = s[s.duplicated(keep=False) & s.notna()]
        if dups.empty:
            res["primary_key_candidates"].append(f["name"])
        else:
            res["duplicated_in"][f["name"]] = _plain_list(sorted(dups.unique())[:10])
    return res


def merge_curated(fresh: list[dict], curated: list[dict]) -> tuple[list[dict], dict]:
    """Combine a fresh profile with a frozen curated dictionary: refresh only
    profiler-owned statistics, preserve every human-authored slot. Returns
    `(merged_fields, drift_report)`.

    Inputs are not modified. Because only curated slots are enforced, a
    steward's *deletion* of one (removing an enum that should never have been
    there) survives re-profiling: the suggestion may reappear as advisory
    output, but nothing enforces it.
    """
    cur = {f["name"]: f for f in curated}
    merged: list[dict] = []
    drift: dict = {
        "new_fields": [],
        "removed_fields": [],
        "type_changed": [],
        "new_unmapped_values": {},
        "unverifiable_vocabularies": [],
    }

    for f in fresh:
        name = f["name"]
        c = cur.get(name)
        if c is None:
            drift["new_fields"].append(name)
            merged.append(dict(f))
            continue
        # Start from the refreshed profile, then overlay every curated slot the
        # profiler does not own (definition, unit, allowed_range,
        # controlled_vocabulary, value_map, enum_canonical, required, plus any
        # extra column a steward added such as sensitivity or derivation_logic).
        row = dict(f)
        for k, v in c.items():
            if k not in PROFILED_KEYS:
                row[k] = v
        if c.get("logical_type") and c["logical_type"] != f.get("logical_type"):
            drift["type_changed"].append(
                {"field": name, "was": c["logical_type"], "now": f.get("logical_type")}
            )
        # Values in the new data that the curated vocabulary does not cover.
        allowed = _canonical_terms(c)
        if allowed is not None:
            observed = f.get("distinct_values")
            if observed is None:
                # Too many distinct values to enumerate: say so rather than
                # reporting a reassuring empty diff.
                drift["unverifiable_vocabularies"].append(
                    {"field": name, "n_unique": f.get("n_unique"),
                     "reason": f"more than {DISTINCT_RECORD_MAX} distinct values; "
                               "vocabulary coverage not checked"}
                )
            else:
                vmap = c.get("value_map")
                allowed_set = {_norm_token(t).lower() for t in allowed}
                unmapped = sorted({
                    str(v) for v in observed
                    if _to_canonical(v, vmap).lower() not in allowed_set
                })
                if unmapped:
                    drift["new_unmapped_values"][name] = unmapped
        merged.append(row)

    fresh_names = {f["name"] for f in fresh}
    drift["removed_fields"] = [n for n in cur if n not in fresh_names]
    return merged, drift


# --- rendering --------------------------------------------------------------
def _canonical_terms(f: dict) -> list | None:
    """The enforceable vocabulary for a field, or None.

    Only `enum_canonical` -- pinned by a steward -- is enforceable.
    `enum_canonical_suggested` is profiler output that `merge_curated` refreshes
    from whatever the latest batch contained; enforcing it would mean the
    dictionary silently learns to accept new values, which is the opposite of
    freezing. An explicitly empty `enum_canonical` is treated as "no vocabulary
    pinned" rather than "nothing is allowed".
    """
    terms = f.get("enum_canonical")
    return terms if terms else None


def to_datapackage(fields: list[dict], keys: dict, *, name: str, title: str,
                   resource_path: str, n_rows: int, provenance: dict | None = None,
                   version: str = DRAFT_VERSION) -> dict:
    """Frictionless-flavoured Data Package descriptor (the machine-readable half)."""
    return {
        "profile": "tabular-data-package",
        "name": name,
        "title": title,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dictionary_version": version,
        "provenance": provenance or {},
        "resources": [{
            "name": name,
            "path": resource_path,
            "profile": "tabular-data-resource",
            "row_count": n_rows,
            "schema": {
                "fields": [
                    {k: v for k, v in f.items() if k != "issues"} for f in fields
                ],
                "primaryKey": keys.get("primary_key_candidates", []),
            },
        }],
        # `issues` is stripped from the schema above, so fields round-tripped
        # through a datapackage carry none -- read defensively.
        "open_issues": {f["name"]: f["issues"] for f in fields if f.get("issues")},
    }


def _required_cell(f: dict) -> str:
    req = f.get("required")
    if req is None:
        return "?"
    return "yes" if req else "no"


def to_markdown(fields: list[dict], keys: dict, *, title: str, source_file: str,
                n_rows: int, provenance: dict | None = None,
                version: str = DRAFT_VERSION) -> str:
    pk = keys.get("primary_key_candidates", [])
    L = [f"# {title}", "",
         f"- Source table: `{source_file}`",
         f"- Rows: {n_rows} | Columns: {len(fields)}",
         f"- Dictionary version: {version} (generated {date.today().isoformat()})",
         f"- Primary key: {', '.join(pk) or '**none established**'}",
         ""]
    if provenance:
        L += ["## Provenance", ""]
        for k, v in provenance.items():
            L.append(f"- **{k}**: {v}")
        L.append("")
    L += ["## Fields", "",
          "| # | Field | Type | Unit | Req. | Missing | Distinct | Domain | Definition |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, f in enumerate(fields, 1):
        terms = _canonical_terms(f)
        if terms is not None:
            shown = [str(t) for t in terms[:6]]
            dom = "enum: " + ", ".join(shown) + (", ..." if len(terms) > 6 else "")
        elif f.get("enum_canonical_suggested"):
            shown = [str(t) for t in f["enum_canonical_suggested"][:6]]
            dom = "proposed: " + ", ".join(shown) + (", ..." if len(f["enum_canonical_suggested"]) > 6 else "")
        elif "observed_range" in f:
            dom = f"observed {f['observed_range'][0]:g}..{f['observed_range'][1]:g}"
        else:
            dom = "free text" if f.get("logical_type") == "string" else f.get("logical_type", "--")
        n_missing, field_rows = f.get("n_missing"), max(f.get("n_rows") or 0, 1)
        missing_cell = ("--" if n_missing is None
                        else f"{n_missing} ({n_missing / field_rows:.0%})")
        L.append("| {} | `{}` | {} | {} | {} | {} | {} | {} | {} |".format(
            i, f["name"], f.get("logical_type", "--"), f.get("unit") or "--",
            _required_cell(f), missing_cell,
            f.get("n_unique", "--"), dom, f.get("definition", "--")))
    L += ["", "## Data-quality findings", "",
          "Each item is a mechanical observation, not a correction -- resolve upstream, "
          "then freeze the dictionary.", ""]
    any_issue = False
    for f in fields:
        # Fields loaded back from a curated dictionary carry no `issues` key --
        # `to_datapackage` strips it. Render them as clean rather than crashing.
        issues = f.get("issues") or []
        if not issues:
            continue
        any_issue = True
        L.append(f"### `{f['name']}`")
        L += [f"- {msg}" for msg in issues]
        L.append("")
    if keys.get("duplicated_in"):
        any_issue = True
        L += ["### Key integrity", ""]
        for col, vals in keys["duplicated_in"].items():
            L.append(f"- `{col}` looks like an identifier but has duplicates: {vals}")
        L.append("")
    if not any_issue:
        L += ["No mechanical issues detected.", ""]
    L += ["## Sign-off", "",
          "| Item | Owner | Date |", "|---|---|---|",
          "| Field definitions and units filled in |  |  |",
          "| `required` flags set as policy |  |  |",
          "| Vocabularies pinned in `enum_canonical` with a `value_map` |  |  |",
          "| Ontology term IDs verified against the source ontology |  |  |",
          "| Permitted ranges confirmed against SOP |  |  |",
          "| Quality findings resolved or accepted |  |  |", ""]
    return "\n".join(L)


# --- validation -------------------------------------------------------------
def validate(df: pd.DataFrame, datapackage: dict) -> pd.DataFrame:
    """Check a batch against a frozen dictionary. One row per violation."""
    schema = datapackage["resources"][0]["schema"]
    spec = {f["name"]: f for f in schema["fields"]}
    rows: list[dict] = []

    def add(sev, field, check, detail):
        rows.append({"severity": sev, "field": field, "check": check, "detail": detail})

    # An empty extract satisfies every per-value constraint vacuously, so say so
    # explicitly rather than returning a clean report for a truncated file.
    if len(df) == 0:
        add("error", "<table>", "row_count", "batch has 0 rows -- nothing to validate")

    for missing in [c for c in spec if c not in df.columns]:
        add("error", missing, "column_present", "column absent from batch")
    for extra in [c for c in df.columns if c not in spec]:
        add("warning", extra, "column_known", "column not in dictionary")

    for name, f in spec.items():
        if name not in df.columns:
            continue
        s = df[name]
        nonnull = s.dropna()
        if _is_text(s):
            nonnull = nonnull[nonnull.astype(str).str.strip() != ""]
        if f.get("required") and len(nonnull) < len(s):
            add("error", name, "required", f"{len(s) - len(nonnull)} missing value(s)")

        terms = _canonical_terms(f)
        if terms is not None and len(nonnull):
            ltype = f.get("logical_type")
            allowed = {_to_canonical(t, None, ltype).lower() for t in terms}
            vmap = f.get("value_map")
            bad = sorted({_norm_token(v) for v in nonnull.unique()
                          if _to_canonical(v, vmap, ltype).lower() not in allowed})
            if bad:
                add("error", name, "enum", f"values outside vocabulary: {bad[:10]}")

        rng = f.get("allowed_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2 and len(nonnull):
            lo, hi = rng
            # A numeric field can arrive as text (CSV read with dtype=str, an
            # Excel export, a database TEXT column). Comparing str to int raises
            # TypeError, so coerce first and report anything non-numeric as its
            # own violation rather than crashing the whole run.
            numeric = pd.to_numeric(nonnull, errors="coerce")
            nonnumeric = sorted({str(v) for v in nonnull[numeric.isna()].unique()})
            if nonnumeric:
                add("error", name, "type",
                    f"{len(nonnumeric)} non-numeric value(s) in a ranged field: {nonnumeric[:5]}")
            numeric = numeric.dropna()
            # A one-sided bound (`[null, 100]`) constrains only that side.
            mask = pd.Series(False, index=numeric.index)
            if lo is not None:
                mask |= numeric < lo
            if hi is not None:
                mask |= numeric > hi
            bad_vals = numeric[mask]
            if len(bad_vals):
                add("error", name, "range",
                    f"{len(bad_vals)} value(s) outside [{lo}, {hi}]: "
                    f"{_plain_list(sorted(bad_vals.unique())[:5])}")

    for pk in schema.get("primaryKey", []):
        if pk in df.columns:
            d = df[pk][df[pk].duplicated(keep=False) & df[pk].notna()]
            if len(d):
                add("error", pk, "primary_key_unique",
                    f"duplicate keys: {_plain_list(sorted(d.unique())[:10])}")

    return pd.DataFrame(rows, columns=["severity", "field", "check", "detail"])
