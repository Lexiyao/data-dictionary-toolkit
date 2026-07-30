"""Regenerate every dictionary artifact from the raw table, and demonstrate the
curation lifecycle end to end.

Run: python rebuild.py
"""
import copy
from pathlib import Path

import pandas as pd
import yaml

import dictgen as dg

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"

CURATED_VERSION = "2.0.0"

# --- steward curation -------------------------------------------------------
# Everything below is a human decision the profiler cannot make: what a field
# means, what unit it is in, whether it is mandatory, what its permitted range
# is, and which raw values are synonyms for which canonical term.
#
# `enum_canonical` is the enforced vocabulary. A field with no `enum_canonical`
# has no vocabulary constraint, whatever the profiler proposed.
CURATION = {
    "sample_id": {
        "definition": "Unique laboratory accession for one aliquot.",
        "required": True,
    },
    "subject_id": {
        "definition": "De-identified donor code; many samples per donor.",
        "required": True,
    },
    "plate_well": {
        "definition": "Plate well position, as row letter and column number.",
        "required": True,
    },
    "tissue": {
        "definition": "Anatomical site of biopsy.",
        "required": True,
        "enum_canonical": ["kidney", "kidney cortex", "liver", "lung"],
        "value_map": {"Kidney": "kidney", "kidney": "kidney", "Liver": "liver",
                      "liver": "liver", "lung": "lung",
                      "kidney cortex": "kidney cortex",
                      "renal cortex": "kidney cortex"},
        "controlled_vocabulary": ("UBERON: kidney=UBERON:0002113, "
                                  "kidney cortex=UBERON:0001225, liver=UBERON:0002107, "
                                  "lung=UBERON:0002048"),
    },
    "disease": {
        "definition": "Diagnosis at collection.",
        "required": False,
        "enum_canonical": ["kirc", "normal"],
        "value_map": {"KIRC": "kirc", "kirc": "kirc",
                      "Normal": "normal", "normal": "normal"},
        "controlled_vocabulary": "MONDO: kirc=MONDO:0005005; normal=PATO:0000461",
    },
    "sex": {
        "definition": "Donor sex, as recorded at collection.",
        "required": False,
        "enum_canonical": ["female", "male"],
        # 1/2 assumed male/female -- MUST be confirmed against the collection
        # SOP before real use; a wrong assumption silently inverts the variable.
        "value_map": {"1": "male", "2": "female", "M": "male", "F": "female",
                      "male": "male", "female": "female"},
        "controlled_vocabulary": "PATO: female=PATO:0000383, male=PATO:0000384",
    },
    "age_at_collection": {
        "definition": "Donor age at biopsy.",
        "unit": "years",
        "required": False,
        "allowed_range": [18, 100],
    },
    "collection_date": {
        "definition": "Date of biopsy. Store ISO-8601 (YYYY-MM-DD).",
        "required": True,
    },
    "batch": {
        "definition": "Processing batch the aliquot was prepared in.",
        "required": True,
        "enum_canonical": ["b1", "b2", "b3"],
        "value_map": {"B1": "b1", "B2": "b2", "B3": "b3"},
    },
    "rin_score": {
        "definition": "RNA integrity number from Bioanalyzer.",
        "unit": "dimensionless",
        "required": False,
        "allowed_range": [1.0, 10.0],
    },
    "treatment": {
        "definition": "Ex-vivo treatment arm.",
        "required": True,
        "enum_canonical": ["drug_a", "drug_b", "vehicle"],
        "value_map": {"Drug_A": "drug_a", "drug_a": "drug_a", "drug_b": "drug_b",
                      "Vehicle": "vehicle", "vehicle": "vehicle"},
    },
    "library_prep": {
        "definition": "Library preparation kit. Constant in this extract; "
                      "belongs in dataset-level metadata if it stays constant.",
        "required": True,
        "enum_canonical": ["truseq_stranded_mrna"],
        "value_map": {"TruSeq_stranded_mRNA": "truseq_stranded_mrna"},
    },
    "operator_notes": {
        "definition": "Free-text technician remark; optional.",
        "required": False,
    },
    # No enum: a boolean is a flag, not a vocabulary. An earlier dictionary
    # pinned true/false here, which broke the moment the column was exported
    # through SQLite as 0/1.
    "qc_pass": {
        "definition": "QC outcome for the aliquot; true = passed.",
        "required": True,
    },
}

PROVENANCE = {
    "generated_by": "dictgen.profile_table + steward curation (rebuild.py)",
    "source_artifact": "sample_manifest_raw.csv",
    "data_status": "SYNTHETIC demonstration data -- not a real cohort",
    "curation_note": (
        f"v{CURATED_VERSION}: vocabularies pinned in `enum_canonical` (only pinned "
        "sets are enforced); `required` set as policy; permitted ranges from SOP; "
        "no vocabulary on boolean qc_pass. sex 1/2 assumed male/female pending "
        "SOP confirmation."
    ),
}


def curate(fields):
    """Apply the steward's decisions to a fresh profile. Returns new dicts."""
    out = []
    for f in fields:
        row = copy.deepcopy(f)
        row.update(copy.deepcopy(CURATION.get(f["name"], {})))
        # `value_map_suggested` is advisory profiler output; the curated
        # `value_map` above is what validation uses.
        row.pop("value_map_suggested", None)
        out.append(row)
    return out


def main():
    OUT.mkdir(exist_ok=True)
    raw = pd.read_csv(DATA / "sample_manifest_raw.csv")
    batch2 = pd.read_csv(DATA / "sample_manifest_batch2.csv")

    fresh = dg.profile_table(raw)
    keys = dg.key_candidates(raw, fresh)

    # --- draft: what the profiler alone can say ----------------------------
    draft = dg.to_datapackage(fresh, keys, name="sample_manifest",
                              title="Sample manifest (demo)",
                              resource_path="data/sample_manifest_raw.csv",
                              n_rows=len(raw), provenance=PROVENANCE)
    with open(OUT / "datapackage.yaml", "w") as fh:
        yaml.safe_dump(draft, fh, sort_keys=False, allow_unicode=True, width=100)

    # --- curated: the steward's decisions, frozen and versioned ------------
    curated_fields = curate(fresh)
    # The primary key must actually be unique in the reference table before it
    # can be frozen as one.
    pk = keys["primary_key_candidates"]
    curated = dg.to_datapackage(curated_fields, {"primary_key_candidates": pk},
                                name="sample_manifest", title="Sample manifest (demo)",
                                resource_path="data/sample_manifest_raw.csv",
                                n_rows=len(raw), provenance=PROVENANCE,
                                version=CURATED_VERSION)
    with open(OUT / "datapackage_curated.yaml", "w") as fh:
        yaml.safe_dump(curated, fh, sort_keys=False, allow_unicode=True, width=100)

    # --- human-readable dictionary, rendered from the CURATED view ----------
    with open(OUT / "DATA_DICTIONARY.md", "w") as fh:
        fh.write(dg.to_markdown(curated_fields, keys,
                                title="Data dictionary: sample manifest (demo)",
                                source_file="data/sample_manifest_raw.csv",
                                n_rows=len(raw), provenance=PROVENANCE,
                                version=CURATED_VERSION))

    # --- proof 1: the source table validates against its own dictionary ----
    self_check = dg.validate(raw, curated)
    self_check.to_csv(OUT / "validation_raw_after.csv", index=False)
    print("=== raw table vs its own frozen dictionary ===")
    print(self_check.to_string(index=False) if len(self_check) else "clean")
    enum_errs = int((self_check["check"] == "enum").sum())
    print(f"\nvocabulary violations on the source table: {enum_errs} (must be 0)")
    if pk:
        print(f"note: `{pk[0]}` frozen as primary key; "
              f"{'duplicates present -- see report above' if (self_check['check'] == 'primary_key_unique').any() else 'unique'}")

    # --- proof 2: a new batch is checked against the frozen dictionary ------
    report = dg.validate(batch2, curated)
    report.to_csv(OUT / "validation_report_batch2.csv", index=False)
    print("\n=== batch 2 vs the frozen dictionary ===")
    print(report.to_string(index=False))

    # --- proof 3: re-profiling cannot widen the vocabulary -----------------
    merged, drift = dg.merge_curated(dg.profile_table(batch2),
                                     curated["resources"][0]["schema"]["fields"])
    by = {f["name"]: f for f in merged}
    print("\n=== re-profiling on batch 2, merged with the frozen dictionary ===")
    print("tissue vocabulary after merge:", by["tissue"].get("enum_canonical"))
    print("definitions preserved:", by["age_at_collection"]["definition"])
    print("drift -- values the vocabulary does not cover:", drift["new_unmapped_values"])
    still_caught = dg.validate(batch2, {"resources": [{"schema": {
        "fields": merged, "primaryKey": pk}}]})
    print("violations still caught after merge:", len(still_caught))


if __name__ == "__main__":
    main()
