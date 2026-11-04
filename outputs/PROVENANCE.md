# Provenance record

Generated 2026-11-04T15:46:02+00:00 by `make_ro_crate.py`.

This records what the repository can prove: each artifact's SHA-256, its size, and the inputs it was derived from. It makes no claim about the execution environment, which a checkout cannot capture -- see `requirements.txt` for the dependencies the code was written against.

**Data status: SYNTHETIC.** The sample manifests are generated demonstration data. No real cohort, and no personal data, appears in this repository.

## Artifact register

| File | SHA-256 (first 16) | Bytes | Derived from |
|---|---|---|---|
| `DESIGN_NOTES.md` | `157aa4190902a34f` | 7241 | -- |
| `README.md` | `df5dfe41b7a096ba` | 5283 | -- |
| `data/sample_manifest_batch2.csv` | `289e589d885f16f7` | 2180 | -- |
| `data/sample_manifest_raw.csv` | `76e9420d3d530c36` | 11159 | -- |
| `dictgen.py` | `2cd47c4e92b647e2` | 27441 | -- |
| `make_ro_crate.py` | `97b0e77c78e49607` | 8264 | -- |
| `outputs/DATA_DICTIONARY.md` | `9d9e9e1edc4dad59` | 4112 | `data/sample_manifest_raw.csv`, `dictgen.py`, `rebuild.py` |
| `outputs/datapackage.yaml` | `127a17db5a3aaf75` | 11238 | `data/sample_manifest_raw.csv`, `dictgen.py`, `rebuild.py` |
| `outputs/datapackage_curated.yaml` | `38acf51ed9db119b` | 11334 | `outputs/datapackage.yaml`, `rebuild.py` |
| `outputs/validation_raw_after.csv` | `56d39f5dff020ed3` | 218 | `data/sample_manifest_raw.csv`, `outputs/datapackage_curated.yaml`, `dictgen.py` |
| `outputs/validation_report_batch2.csv` | `1b09c4d224fb408a` | 393 | `data/sample_manifest_batch2.csv`, `outputs/datapackage_curated.yaml`, `dictgen.py` |
| `rebuild.py` | `98cac88ac30eebd3` | 9192 | -- |
| `test_dictgen.py` | `fa6faddc8e2a98ea` | 14103 | -- |
| `validate_sql.py` | `4bedd27ce47eae13` | 6522 | -- |

## Reproduction

```bash
pip install -r requirements.txt
python rebuild.py         # regenerates everything in outputs/
python make_ro_crate.py   # regenerates this record
python test_dictgen.py    # or: pytest
```

## Dictionary lifecycle

1. `dictgen.profile_table` profiles the raw table -> `outputs/datapackage.yaml` (draft; `TODO` slots for definitions, units, permitted ranges, ontology terms).
2. A steward fills those slots and pins each vocabulary in `enum_canonical` -> `outputs/datapackage_curated.yaml`, versioned. Here the curation is held as data in `rebuild.py`, so it can be reviewed and re-applied.
3. Each later batch is checked against the frozen dictionary with `dictgen.validate` -> `outputs/validation_report_*.csv`.
4. `dictgen.merge_curated` refreshes profiled statistics from new data without touching curated slots, and reports drift. It cannot widen an enforced vocabulary; that takes a steward and a version bump.
