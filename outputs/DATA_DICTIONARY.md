# Data dictionary: sample manifest (demo)

- Source table: `data/sample_manifest_raw.csv`
- Rows: 120 | Columns: 14
- Dictionary version: 2.0.0 (generated 2026-07-30)
- Primary key: **none established**

## Provenance

- **generated_by**: dictgen.profile_table + steward curation (rebuild.py)
- **source_artifact**: sample_manifest_raw.csv
- **data_status**: SYNTHETIC demonstration data -- not a real cohort
- **curation_note**: v2.0.0: vocabularies pinned in `enum_canonical` (only pinned sets are enforced); `required` set as policy; permitted ranges from SOP; no vocabulary on boolean qc_pass. sex 1/2 assumed male/female pending SOP confirmation.

## Fields

| # | Field | Type | Unit | Req. | Missing | Distinct | Domain | Definition |
|---|---|---|---|---|---|---|---|---|
| 1 | `sample_id` | string | -- | yes | 0 (0%) | 119 | free text | Unique laboratory accession for one aliquot. |
| 2 | `subject_id` | string | -- | yes | 2 (2%) | 80 | free text | De-identified donor code; many samples per donor. |
| 3 | `plate_well` | string | -- | yes | 0 (0%) | 58 | free text | Plate well position, as row letter and column number. |
| 4 | `tissue` | string | -- | yes | 0 (0%) | 7 | enum: kidney, kidney cortex, liver, lung | Anatomical site of biopsy. |
| 5 | `disease` | string | -- | no | 4 (3%) | 4 | enum: kirc, normal | Diagnosis at collection. |
| 6 | `sex` | string | -- | no | 5 (4%) | 6 | enum: female, male | Donor sex, as recorded at collection. |
| 7 | `age_at_collection` | number | years | no | 3 (2%) | 49 | observed 30..217 | Donor age at biopsy. |
| 8 | `collection_date` | date-like string | -- | yes | 0 (0%) | 4 | date-like string | Date of biopsy. Store ISO-8601 (YYYY-MM-DD). |
| 9 | `batch` | string | -- | yes | 0 (0%) | 3 | enum: b1, b2, b3 | Processing batch the aliquot was prepared in. |
| 10 | `rin_score` | number | dimensionless | no | 14 (12%) | 42 | observed -3.2..10.3 | RNA integrity number from Bioanalyzer. |
| 11 | `treatment` | string | -- | yes | 0 (0%) | 6 | enum: drug_a, drug_b, vehicle | Ex-vivo treatment arm. |
| 12 | `library_prep` | string | -- | yes | 0 (0%) | 1 | enum: truseq_stranded_mrna | Library preparation kit. Constant in this extract; belongs in dataset-level metadata if it stays constant. |
| 13 | `operator_notes` | string | -- | no | 111 (92%) | 1 | free text | Free-text technician remark; optional. |
| 14 | `qc_pass` | boolean | -- | yes | 0 (0%) | 2 | boolean | QC outcome for the aliquot; true = passed. |

## Data-quality findings

Each item is a mechanical observation, not a correction -- resolve upstream, then freeze the dictionary.

### `subject_id`
- 2/120 missing (1.7%)

### `tissue`
- leading/trailing whitespace present
- case/whitespace variants collapse 7 -> 5 values, e.g. [['Kidney', 'kidney'], ['Liver ', 'liver']]

### `disease`
- 4/120 missing (3.3%)
- case/whitespace variants collapse 4 -> 2 values, e.g. [['KIRC', 'kirc'], ['Normal', 'normal']]

### `sex`
- 5/120 missing (4.2%)

### `age_at_collection`
- 3/120 missing (2.5%)
- 1 value(s) beyond 3xIQR fence [-34, 148]: [217.0] -- confirm plausible range

### `collection_date`
- mixed date formats: ['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y or %m/%d/%Y (ambiguous)', '%b %d %Y']

### `rin_score`
- 14/120 missing (11.7%)
- 1 value(s) beyond 3xIQR fence [3.3, 12.4]: [-3.2] -- confirm plausible range

### `treatment`
- leading/trailing whitespace present
- case/whitespace variants collapse 6 -> 3 values, e.g. [['Drug_A', 'drug_a'], [' vehicle', 'Vehicle', 'vehicle']]

### `library_prep`
- single constant value -- belongs in dataset-level metadata, not a column

### `operator_notes`
- 111/120 missing (92.5%)

### Key integrity

- `sample_id` looks like an identifier but has duplicates: ['S0099']

## Sign-off

| Item | Owner | Date |
|---|---|---|
| Field definitions and units filled in |  |  |
| `required` flags set as policy |  |  |
| Vocabularies pinned in `enum_canonical` with a `value_map` |  |  |
| Ontology term IDs verified against the source ontology |  |  |
| Permitted ranges confirmed against SOP |  |  |
| Quality findings resolved or accepted |  |  |
