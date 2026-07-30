# Data dictionary, controlled vocabulary and provenance toolkit

Turn a raw research table into a **documented, validated, analysis-ready
dataset** — and keep it that way as new batches arrive.

The demo data is **synthetic** (a renal-cancer sample manifest). No real cohort
data, and no personal data, is in this repository.

## Why

Research tables arrive with the same problems: the same concept spelled four
ways, dates in three formats, a "unique" ID that isn't, an out-of-range value
nobody noticed. Fixing them once is easy. Making sure the *next* batch doesn't
reintroduce them is the hard part, and that is what a frozen data dictionary is
for.

## Lifecycle

```
raw table
   │  profile_table()          mechanical facts only: types, domains,
   ▼                           missingness, key candidates, hygiene flags
draft dictionary  (outputs/datapackage.yaml, 0.1.0-draft)
   │  human curation           definitions, units, permitted ranges,
   ▼                           required flags, pinned vocabularies + value maps
curated dictionary (outputs/datapackage_curated.yaml, 2.0.0)  ← frozen, versioned
   │  validate()               every later batch is checked against it
   ▼
validation report (one row per violation)
```

`merge_curated()` refreshes profiled statistics when new data arrives, preserves
every curated slot, and reports drift — new or removed fields, type changes, and
values the pinned vocabulary does not cover. It cannot widen an enforced
vocabulary; that takes a steward and a version bump.

## Quick start

```bash
pip install -r requirements.txt

python rebuild.py         # regenerate every artifact in outputs/
python validate_sql.py    # run the same constraints as SQL, and diff the reports
python make_ro_crate.py   # regenerate the provenance record + RO-Crate
python test_dictgen.py    # 28 regression tests (or: pytest)
```

## Library use

```python
import pandas as pd, yaml, dictgen as dg

df = pd.read_csv("data/sample_manifest_raw.csv")

# 1. profile -> draft dictionary
fields = dg.profile_table(df)
keys   = dg.key_candidates(df, fields)
draft  = dg.to_datapackage(fields, keys, name="sample_manifest",
                           title="Sample manifest",
                           resource_path="data/sample_manifest_raw.csv",
                           n_rows=len(df))

# 2. a steward fills the TODO slots, pins each vocabulary in `enum_canonical`,
#    and freezes the result (see the CURATION block in rebuild.py)

# 3. check every later batch against the frozen dictionary
curated = yaml.safe_load(open("outputs/datapackage_curated.yaml"))
report  = dg.validate(pd.read_csv("data/sample_manifest_batch2.csv"), curated)

# 4. when a new extract arrives, merge instead of re-profiling blind
merged, drift = dg.merge_curated(dg.profile_table(new_df),
                                 curated["resources"][0]["schema"]["fields"])
```

## What a validation report looks like

```
severity  field              check               detail
warning   extra_flowcell_id  column_known        column not in dictionary
error     subject_id         required            2 missing value(s)
error     tissue             enum                values outside vocabulary: ['spleen']
error     disease            enum                values outside vocabulary: ['KIRP']
error     age_at_collection  range               2 value(s) outside [18, 100]: [12.0, 217.0]
error     sample_id          primary_key_unique  duplicate keys: ['S0203']
```

## Layout

```
dictgen.py          profile / render / merge / validate — the library
validate_sql.py     the same constraints as generated SQL, run on SQLite
rebuild.py          driver; also holds the steward curation as reviewable data
make_ro_crate.py    provenance record + RO-Crate descriptor
test_dictgen.py     28 regression tests
data/               synthetic demonstration manifests
outputs/            generated: dictionaries, validation reports, provenance
```

## Design

The reasoning behind the main decisions — and the bugs that taught me each one —
is in [DESIGN_NOTES.md](DESIGN_NOTES.md). The short version:

- **The profiler proposes; only a steward disposes.** Every enforced constraint
  lives in a curated slot the profiler never writes to.
- **A suggestion never gates validation.** When the profiler's *suggested*
  vocabulary was enforceable, re-profiling silently widened the dictionary to
  admit whatever had just arrived — while the validation report stayed clean.
- **`required` is a policy assertion**, not an artefact of whichever extract you
  happened to profile.
- **Vocabularies need an explicit raw-to-canonical map**, not case folding.
- **Report, never repair.** Silent repair moves the error from the data, where
  someone might notice, into the tool, where nobody will.

## Standards

[Frictionless Table Schema](https://specs.frictionlessdata.io/table-schema/) for
the machine-readable dictionary; [RO-Crate](https://www.researchobject.org/ro-crate/)
for packaging and provenance; FAIR principles for the documentation layer;
OMOP/FHIR-style concept mapping for controlled vocabularies.

## Licence

MIT — see [LICENSE](LICENSE).
