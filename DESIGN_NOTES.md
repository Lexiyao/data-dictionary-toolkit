# Design notes

Why the tool works the way it does. Most of these rules exist because an earlier
version of the code broke the rule and I had to find out why it mattered.

## The profiler proposes; only a steward disposes

Every constraint `validate()` enforces lives in a curated slot, and the profiler
never writes to those slots:

| profiler-owned (refreshed every run) | curated (enforced, never overwritten) |
|---|---|
| `logical_type`, `storage_dtype` | `definition`, `unit` |
| `n_rows`, `n_missing`, `n_unique` | `required` |
| `example_values`, `observed_range` | `allowed_range` |
| `distinct_values`, `enum_observed` | `enum_canonical` + `value_map` |
| `enum_canonical_suggested` *(advisory)* | `controlled_vocabulary` |
| `profiled_complete`, `issues` | anything else a steward adds |

## A suggestion must never gate validation

This was the worst bug in the project, and the most instructive.

`enum_canonical_suggested` is profiler output, so `merge_curated` refreshes it
from each new batch, which is correct in isolation. But `_canonical_terms` used
to fall back to it when no `enum_canonical` was pinned. Those two reasonable
decisions combined into an unreasonable system: **the refreshed suggestion
became the enforced vocabulary.**

Concretely. A curated `batch` vocabulary of `[b1, b2, b3]` meets a new extract
containing `B9`. Re-profile, merge, freeze. The artifact now permits `B9`, and
the validation report is clean. The dictionary had quietly taught itself to
accept the thing it existed to catch.

It was worse for deletions. A steward removed the vocabulary from a boolean
`qc_pass` column, because exporting it through SQLite turned `true`/`false` into
`0`/`1` and every row failed. One merge later the vocabulary was back: *absence
cannot be preserved as a curated slot* when the slot regenerates itself.

The fix is a rule, not a patch: only a pinned `enum_canonical` is ever
enforceable. Suggestions render as `proposed:` in the human-readable dictionary
and are inert everywhere else. Deletion now survives by construction.

A frozen dictionary that quietly unfreezes itself is worse than no dictionary,
because the clean validation report tells you everything is fine.

## `required` is a policy assertion, not an observation

Inferring `required` from whether a given extract had nulls conflates "mandatory
by policy" with "this extract happened to be complete". A donor-link column
appears in one batch with two missing values and silently becomes optional; a
constraint flips between batches with nobody deciding anything.

The profiler records `profiled_complete` (what it saw) and leaves `required` as
`None` until a human sets it. `validate()` skips unset flags; it does not guess.

## Vocabularies need a raw-to-canonical map

`M`, `1` and `male` are one concept. `renal cortex` and `kidney cortex` may be
one concept, or may not. Only a domain expert can say. Case-folding alone
cannot express any of that, and a vocabulary built by case-folding will reject
the very table it was derived from.

`value_map` records the human decisions; `validate()` maps values through it
*before* testing membership. This is the same shape as OMOP's
`CONCEPT_RELATIONSHIP` and FHIR's `ConceptMap`, for the same reason.

## Normalise both sides of every comparison

A `sex` column coded `1`/`2` is `int64` in a complete extract and `float64` the
moment one row goes missing. A naive `str()` then yields `'1'` and later
`'1.0'`, and *every row in the column* falls out of a vocabulary that has not
changed. YAML compounds it: `value_map: {1: male}` parses to integer keys, so a
string-to-integer comparison silently matches nothing.

Values and vocabulary terms therefore pass through one normalisation function.
Integral floats lose the decimal tail; booleans fold to `true`/`false` so a
0/1 database export still compares equal. The profiler no longer proposes
vocabularies for numeric or boolean columns at all: a quantity is not a
vocabulary, and a flag is not a vocabulary.

## Ask whether a column is text, not which dtype holds it

pandas 3 reads text columns as the `str` dtype, where pandas 2 used `object`.
Every text check in the profiler and the validator was gated on
`s.dtype == object`, so under pandas 3 none of them ran: the blank-cell filter,
the prose test, and every hygiene flag in the text block (whitespace, case
variants, mixed date formats, zero-padded codes, sentinels). `requirements.txt`
says `pandas>=2.0`, so this is what a fresh install got.

On the demo data the damage was visible only in the dictionary: `rebuild.py`
exited 0 and the tissue, disease, collection_date and treatment findings were
gone from `DATA_DICTIONARY.md`. The validation reports did not change, because
the two missing `subject_id` values are empty CSV fields, which `read_csv`
turns into NaN under either version, and NaN is counted without consulting the
dtype. A blank *string* in a required column is a different matter: it
validated clean, and `test_text_checks_run_whatever_dtype_carries_the_strings`
pins that case.

That is the same failure as the dictionary that unfroze itself: a clean report
produced by a check that did not run. The gate is now `_is_text()`, which asks
whether the column holds text (object, any pandas string dtype, or a
categorical whose categories are strings) rather than which dtype this pandas
version chose for it. `storage_dtype` still records the dtype as seen, so a
dictionary rebuilt under pandas 3 shows `str` where an older one shows
`object`; that is a true statement about storage, not drift.

## Report, never repair

The tool never silently fixes data. It reports what it finds; a human resolves
it upstream and then freezes the dictionary. Silent repair moves the error from
the data, where someone might notice it, into the tool, where nobody will.

The same instinct applies to what the tool *cannot* check. If a column has too
many distinct values to enumerate, `merge_curated` says so explicitly, and does
not return a reassuring empty diff. An earlier version reported "no drift" on a
column with 120 unmapped values, because the field it consulted had vanished
when the column stopped looking like an enum.

## Two implementations, cross-checked

The constraints are implemented twice: once in pandas (`dictgen.validate`) and
once as generated SQL (`validate_sql.validate_sql`). Running both on the same
data and diffing the reports is a cheap, strong correctness check. It is how the
boolean 0/1 problem was found, because the two implementations disagreed before
either was obviously wrong.

## What this does not do

- Single-table, field-level only. No referential integrity across tables, and
  no cross-field rules such as `date_of_death >= date_of_birth`.
- Profiling is row-wise Python. Fine to ~10^5 rows; sample larger tables.
- Ontology term IDs are hand-entered and unverified: the tool does not resolve
  them against the source ontology.
- Semantic decisions in the demo curation are assumptions, and are labelled as
  such. `sex` coded 1/2 is mapped assuming 1=male, 2=female; in real use that
  must be confirmed against the data-collection SOP, because a wrong assumption
  silently inverts a variable and nothing downstream will notice.
