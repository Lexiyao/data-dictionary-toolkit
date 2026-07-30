"""Emit an RO-Crate metadata descriptor and a human-readable provenance record
for the artifacts in this repository.

RO-Crate (https://www.researchobject.org/ro-crate/) packages a set of files with
machine-readable metadata describing what each one is and how it was derived. It
is the FAIR-community convention for making a folder of research outputs
self-describing.

Provenance here is deliberately modest: it records what this repository can
actually prove -- each file's SHA-256, its size, and which inputs it was derived
from. It makes no claim about the execution environment, because a checkout
cannot capture one.

Outputs: outputs/ro-crate-metadata.json, outputs/PROVENANCE.md
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"

CONTEXT = "https://w3id.org/ro/crate/1.1/context"

DESCRIPTIONS = {
    "dictgen.py": "Profiling, rendering, merging and validation library for tabular data dictionaries.",
    "validate_sql.py": "SQL expression of the dictionary constraints, run against SQLite.",
    "test_dictgen.py": "Regression tests for vocabulary, required-flag and curation-merge semantics.",
    "rebuild.py": "Driver that regenerates every artifact from the raw table, and holds the steward curation.",
    "make_ro_crate.py": "Generates the RO-Crate metadata descriptor and this provenance record.",
    "README.md": "Project overview, lifecycle and design decisions.",
    "DESIGN_NOTES.md": "Why the tool is built the way it is, including what an earlier version got wrong.",
    "data/sample_manifest_raw.csv": "SYNTHETIC demonstration sample manifest. Not a real cohort.",
    "data/sample_manifest_batch2.csv": "SYNTHETIC second batch, used to demonstrate validation. Not a real cohort.",
    "outputs/DATA_DICTIONARY.md": "Human-readable data dictionary: field table, quality findings, sign-off block.",
    "outputs/datapackage.yaml": "Machine-readable draft dictionary (Frictionless-flavoured) with TODO slots.",
    "outputs/datapackage_curated.yaml": "Frozen, curated, versioned dictionary. The enforced constraints live here.",
    "outputs/validation_report_batch2.csv": "Violations found when batch 2 was checked against the frozen dictionary.",
    "outputs/validation_raw_after.csv": "The source table checked against its own frozen dictionary.",
}

# derivation edges: output -> inputs
DERIVED_FROM = {
    "outputs/datapackage.yaml": ["data/sample_manifest_raw.csv", "dictgen.py", "rebuild.py"],
    "outputs/DATA_DICTIONARY.md": ["data/sample_manifest_raw.csv", "dictgen.py", "rebuild.py"],
    "outputs/datapackage_curated.yaml": ["outputs/datapackage.yaml", "rebuild.py"],
    "outputs/validation_raw_after.csv": ["data/sample_manifest_raw.csv",
                                         "outputs/datapackage_curated.yaml", "dictgen.py"],
    "outputs/validation_report_batch2.csv": ["data/sample_manifest_batch2.csv",
                                             "outputs/datapackage_curated.yaml", "dictgen.py"],
}

MEDIA_TYPES = {".py": "text/x-python", ".md": "text/markdown",
               ".csv": "text/csv", ".yaml": "application/x-yaml",
               ".json": "application/json"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_crate(files: list[str]) -> dict:
    graph = [
        {"@type": "CreativeWork", "@id": "ro-crate-metadata.json",
         "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
         "about": {"@id": "./"}},
        {
            "@id": "./",
            "@type": "Dataset",
            "name": "Data dictionary, controlled vocabulary and provenance toolkit",
            "description": (
                "Turns a raw research table into a documented, validated, analysis-ready "
                "dataset: mechanical profiling, a curated and frozen data dictionary with "
                "controlled vocabularies, batch validation against that dictionary, and a "
                "provenance record. Demonstration data is synthetic."
            ),
            "license": {"@id": "https://spdx.org/licenses/MIT.html"},
            "hasPart": [{"@id": f} for f in files],
        },
    ]
    for rel in files:
        path = ROOT / rel
        entry = {
            "@id": rel,
            "@type": "File",
            "name": Path(rel).name,
            "description": DESCRIPTIONS.get(rel, ""),
            "encodingFormat": MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
            "contentSize": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if rel in DERIVED_FROM:
            entry["isBasedOn"] = [{"@id": src} for src in DERIVED_FROM[rel]
                                  if (ROOT / src).exists()]
        if "SYNTHETIC" in DESCRIPTIONS.get(rel, "").upper():
            entry["disambiguatingDescription"] = "Synthetic data -- contains no personal data."
        graph.append(entry)
    return {"@context": CONTEXT, "@graph": graph}


def render_provenance(crate: dict) -> str:
    files = [e for e in crate["@graph"] if e.get("@type") == "File"]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    L = [
        "# Provenance record",
        "",
        f"Generated {stamp} by `make_ro_crate.py`.",
        "",
        "This records what the repository can prove: each artifact's SHA-256, its size, "
        "and the inputs it was derived from. It makes no claim about the execution "
        "environment, which a checkout cannot capture -- see `requirements.txt` for the "
        "dependencies the code was written against.",
        "",
        "**Data status: SYNTHETIC.** The sample manifests are generated demonstration "
        "data. No real cohort, and no personal data, appears in this repository.",
        "",
        "## Artifact register",
        "",
        "| File | SHA-256 (first 16) | Bytes | Derived from |",
        "|---|---|---|---|",
    ]
    for e in files:
        srcs = ", ".join(f"`{s['@id']}`" for s in e.get("isBasedOn", [])) or "--"
        L.append(f"| `{e['@id']}` | `{e['sha256'][:16]}` | {e['contentSize']} | {srcs} |")
    L += [
        "",
        "## Reproduction",
        "",
        "```bash",
        "pip install -r requirements.txt",
        "python rebuild.py         # regenerates everything in outputs/",
        "python make_ro_crate.py   # regenerates this record",
        "python test_dictgen.py    # or: pytest",
        "```",
        "",
        "## Dictionary lifecycle",
        "",
        "1. `dictgen.profile_table` profiles the raw table -> `outputs/datapackage.yaml` "
        "(draft; `TODO` slots for definitions, units, permitted ranges, ontology terms).",
        "2. A steward fills those slots and pins each vocabulary in `enum_canonical` "
        "-> `outputs/datapackage_curated.yaml`, versioned. Here the curation is held as "
        "data in `rebuild.py`, so it can be reviewed and re-applied.",
        "3. Each later batch is checked against the frozen dictionary with "
        "`dictgen.validate` -> `outputs/validation_report_*.csv`.",
        "4. `dictgen.merge_curated` refreshes profiled statistics from new data without "
        "touching curated slots, and reports drift. It cannot widen an enforced "
        "vocabulary; that takes a steward and a version bump.",
        "",
    ]
    return "\n".join(L)


def main():
    OUT.mkdir(exist_ok=True)
    tracked = [rel for rel in sorted(DESCRIPTIONS) if (ROOT / rel).exists()]
    crate = build_crate(tracked)
    with open(OUT / "ro-crate-metadata.json", "w") as fh:
        json.dump(crate, fh, indent=2)
        fh.write("\n")
    with open(OUT / "PROVENANCE.md", "w") as fh:
        fh.write(render_provenance(crate))
    print(f"outputs/ro-crate-metadata.json: {len(tracked)} files described")
    print("outputs/PROVENANCE.md written")
    for e in crate["@graph"]:
        if e.get("isBasedOn"):
            print(f"  {e['@id']}  <-  {', '.join(s['@id'] for s in e['isBasedOn'])}")


if __name__ == "__main__":
    main()
