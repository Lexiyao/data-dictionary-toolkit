"""Regression tests for the three fixes: enum value_map, required-as-assertion,
merge_curated preservation. Runs under pytest, or directly: `python test_dictgen.py`."""
import pandas as pd
import dictgen as dg


def _pkg(fields, keys=None, pk=None):
    return {"resources": [{"schema": {
        "fields": fields, "primaryKey": pk or []}}]}


def test_value_map_maps_synonyms_before_membership():
    # A frozen enum with a steward value_map for synonyms.
    field = {
        "name": "sex",
        "enum_canonical": ["female", "male"],
        "value_map": {"1": "male", "2": "female", "M": "male", "F": "female",
                      "male": "male", "female": "female"},
    }
    df = pd.DataFrame({"sex": ["1", "2", "M", "F", "male", "female"]})
    report = dg.validate(df, _pkg([field]))
    assert report.empty, f"synonyms should validate clean, got:\n{report}"

    # An unmapped value is a genuine violation.
    bad = pd.DataFrame({"sex": ["male", "unknown"]})
    report = dg.validate(bad, _pkg([field]))
    assert (report["check"] == "enum").any()
    assert "unknown" in report.iloc[0]["detail"]


def test_source_validates_against_its_own_frozen_dict():
    # The bug: canonical set built by case-fold only, no map -> raw fails itself.
    df = pd.DataFrame({"tissue": ["Kidney", "renal cortex", "kidney cortex"]})
    fixed = {
        "name": "tissue",
        "enum_canonical": ["kidney", "kidney cortex"],
        "value_map": {"Kidney": "kidney", "renal cortex": "kidney cortex",
                      "kidney cortex": "kidney cortex"},
    }
    assert dg.validate(df, _pkg([fixed])).empty


def test_profiler_leaves_required_unset():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [1, None, 3]})
    fields = dg.profile_table(df)
    for f in fields:
        assert f["required"] is None, "required must be a steward assertion, not profiled"
    # the observation is still recorded, just not as the constraint
    assert {f["name"]: f["profiled_complete"] for f in fields} == {"a": True, "b": False}


def test_unset_required_is_not_enforced():
    field = {"name": "a", "required": None}
    df = pd.DataFrame({"a": [1, None, 3]})
    assert dg.validate(df, _pkg([field])).empty  # None -> no required check


def test_merge_preserves_curation_and_refreshes_stats():
    curated = [{
        "name": "age", "logical_type": "number", "n_rows": 10, "n_missing": 0,
        "definition": "Donor age at biopsy.", "unit": "years",
        "allowed_range": [18, 100], "required": True,
    }]
    fresh_df = pd.DataFrame({"age": [40.0, None, 55.0]})  # new extract, 1 missing
    fresh = dg.profile_table(fresh_df)
    merged, drift = dg.merge_curated(fresh, curated)
    m = merged[0]
    assert m["definition"] == "Donor age at biopsy."   # curated slot preserved
    assert m["allowed_range"] == [18, 100]             # preserved
    assert m["required"] is True                        # curated assertion kept
    assert m["n_missing"] == 1                           # profiled stat refreshed
    assert drift["new_fields"] == [] and drift["removed_fields"] == []


def test_merge_reports_drift_for_new_and_removed_fields():
    curated = [{"name": "old", "logical_type": "string", "definition": "x"}]
    fresh = dg.profile_table(pd.DataFrame({"new": [1, 2, 3]}))
    _, drift = dg.merge_curated(fresh, curated)
    assert drift["new_fields"] == ["new"]
    assert drift["removed_fields"] == ["old"]


def test_range_check_survives_a_text_coded_numeric_column():
    # Same numbers, read as text (CSV with dtype=str, Excel export, TEXT column).
    field = {"name": "age", "allowed_range": [18, 100]}
    df = pd.DataFrame({"age": ["45", "200"]})
    report = dg.validate(df, _pkg([field]))          # must not raise TypeError
    assert (report["check"] == "range").any(), "200 should still breach [18, 100]"
    assert "200" in report[report["check"] == "range"].iloc[0]["detail"]


def test_non_numeric_value_in_ranged_field_is_reported_not_fatal():
    field = {"name": "age", "allowed_range": [18, 100]}
    df = pd.DataFrame({"age": ["45", "unknown", "200"]})
    report = dg.validate(df, _pkg([field]))
    assert (report["check"] == "type").any(), "non-numeric entry should be flagged"
    assert (report["check"] == "range").any(), "numeric outliers still checked"


def test_markdown_renders_fields_loaded_back_from_a_curated_dictionary():
    # to_datapackage strips `issues`, so curated fields lack that key.
    field = {"name": "a", "logical_type": "string", "unit": None, "n_rows": 10,
             "n_missing": 0, "n_unique": 3, "definition": "x", "required": True}
    md = dg.to_markdown([field], {"primary_key_candidates": [], "duplicated_in": {}},
                        title="t", source_file="s.csv", n_rows=10)   # must not raise
    assert "`a`" in md and "No mechanical issues detected." in md


def test_only_pinned_vocabulary_is_enforced_never_the_suggestion():
    # A suggestion is profiler output that re-profiling refreshes. If it gated
    # validation, the dictionary would silently learn to accept new values.
    field = {"name": "batch", "enum_canonical_suggested": ["b1", "b2"]}
    df = pd.DataFrame({"batch": ["B1", "ANYTHING"]})
    assert dg.validate(df, _pkg([field])).empty, "a suggestion must not gate validation"
    # Pinned, it is enforced.
    field["enum_canonical"] = ["b1", "b2"]
    field["value_map"] = {"B1": "b1", "B2": "b2"}
    report = dg.validate(df, _pkg([field]))
    assert (report["check"] == "enum").any()


def test_merge_cannot_widen_the_enforced_vocabulary():
    curated = [{"name": "batch", "enum_canonical": ["b1", "b2", "b3"],
                "value_map": {"B1": "b1", "B2": "b2", "B3": "b3"},
                "definition": "Processing batch."}]
    new = pd.DataFrame({"batch": ["B1", "B2", "B9"] * 10})
    merged, drift = dg.merge_curated(dg.profile_table(new), curated)
    assert merged[0]["enum_canonical"] == ["b1", "b2", "b3"], "pinned set must survive"
    assert drift["new_unmapped_values"]["batch"] == ["B9"]
    report = dg.validate(new, _pkg(merged))
    assert (report["check"] == "enum").any(), "B9 must still be caught after a merge"


def test_merge_preserves_a_stewards_deletion_of_a_vocabulary():
    # A steward removed an enum that should never have been there. Re-profiling
    # may re-propose it, but nothing may enforce it again.
    curated = [{"name": "qc_pass", "definition": "QC outcome.", "required": True}]
    fresh = dg.profile_table(pd.DataFrame({"qc_pass": [True] * 8 + [False] * 32}))
    merged, _ = dg.merge_curated(fresh, curated)
    assert dg._canonical_terms(merged[0]) is None, "deleted vocabulary must stay unenforced"
    assert dg.validate(pd.DataFrame({"qc_pass": [0, 1]}), _pkg(merged)).empty


def test_numeric_codes_survive_int_to_float_dtype_drift():
    # `sex` coded 1/2: int64 when complete, float64 as soon as one row is
    # missing. A frozen vocabulary must not depend on which one you got.
    field = {"name": "sex", "enum_canonical": ["1", "2"], "value_map": {"1": "1", "2": "2"}}
    assert dg.validate(pd.DataFrame({"sex": [1, 2]}), _pkg([field])).empty
    assert dg.validate(pd.DataFrame({"sex": [1, 2, None]}), _pkg([field])).empty


def test_unquoted_yaml_keys_still_resolve():
    # `value_map: {1: male}` in YAML gives int keys, not strings.
    field = {"name": "sex", "enum_canonical": ["male", "female"],
             "value_map": {1: "male", 2: "female"}}
    assert dg.validate(pd.DataFrame({"sex": ["1", "2"]}), _pkg([field])).empty


def test_unquoted_yaml_terms_still_resolve():
    # `enum_canonical: [1, 2]` in YAML gives int terms, not strings.
    field = {"name": "code", "enum_canonical": [1, 2], "value_map": {1: 1, 2: 2}}
    assert dg.validate(pd.DataFrame({"code": ["1", "2"]}), _pkg([field])).empty
    assert not dg.validate(pd.DataFrame({"code": ["3"]}), _pkg([field])).empty


def test_value_map_key_with_stray_whitespace_is_reachable():
    field = {"name": "t", "enum_canonical": ["kidney cortex"],
             "value_map": {"renal cortex ": "kidney cortex"}}
    assert dg.validate(pd.DataFrame({"t": ["renal cortex "]}), _pkg([field])).empty


def test_boolean_column_survives_a_zero_one_export():
    field = {"name": "q", "logical_type": "boolean", "enum_canonical": ["true", "false"],
             "value_map": {"True": "true", "False": "false"}}
    assert dg.validate(pd.DataFrame({"q": [True, False]}), _pkg([field])).empty
    assert dg.validate(pd.DataFrame({"q": [1, 0]}), _pkg([field])).empty


def test_profiler_does_not_propose_vocabularies_for_numbers_or_booleans():
    fields = dg.profile_table(pd.DataFrame({
        "flag": [True, False] * 20,
        "code": [1, 2] * 20,
        "word": ["a", "b"] * 20,
    }))
    by = {f["name"]: f for f in fields}
    assert "enum_canonical_suggested" not in by["flag"]
    assert "enum_canonical_suggested" not in by["code"]
    assert "enum_canonical_suggested" in by["word"]


def test_missing_sentinels_are_never_proposed_as_vocabulary_terms():
    df = pd.DataFrame({"disease": (["KIRC", "normal", "kirc", "NA"] * 10)})
    f = dg.profile_table(df)[0]
    assert "na" not in f["enum_canonical_suggested"], "a sentinel must not become a term"
    assert any("sentinel" in msg for msg in f["issues"])


def test_empty_string_cells_are_counted_and_flagged():
    f = dg.profile_table(pd.DataFrame({"c": ["a", "", "", None, "b"]}))[0]
    assert f["n_missing"] == 3
    assert any("empty-string" in m for m in f["issues"])
    joined = " ".join(f["issues"])
    assert "3/5 missing" in joined, f"missingness must agree with n_missing: {f['issues']}"


def test_entirely_blank_column_is_flagged():
    f = dg.profile_table(pd.DataFrame({"c": ["", "", ""]}))[0]
    assert any("entirely empty" in m for m in f["issues"])


def test_text_checks_run_whatever_dtype_carries_the_strings():
    # pandas 3 reads text as the `str` dtype, pandas 2 as `object`. The text
    # checks were gated on `dtype == object`, so under pandas 3 a blank required
    # cell validated clean and every hygiene flag vanished, with exit code 0.
    # `dtype="string"` reproduces that on either major version.
    df = pd.DataFrame({"t": pd.array(["Kidney", "kidney ", " ", "kidney"], dtype="string")})
    f = dg.profile_table(df)[0]
    assert f["n_missing"] == 1, "a blank cell is missing whichever dtype holds it"
    assert any("empty-string" in m for m in f["issues"])
    assert any("variants collapse" in m for m in f["issues"])
    assert any("whitespace present" in m for m in f["issues"])
    report = dg.validate(df, _pkg([{"name": "t", "required": True}]))
    assert (report["check"] == "required").any(), "blank required cell must be reported"


def test_zero_row_batch_is_not_silently_clean():
    field = {"name": "a", "required": True}
    report = dg.validate(pd.DataFrame({"a": pd.Series([], dtype=object)}), _pkg([field]))
    assert (report["check"] == "row_count").any(), "an empty extract must not validate clean"


def test_datapackage_round_trips_through_its_own_output():
    fields = dg.profile_table(pd.DataFrame({"a": [1, 2, 3]}))
    keys = {"primary_key_candidates": [], "duplicated_in": {}}
    pkg = dg.to_datapackage(fields, keys, name="t", title="T",
                            resource_path="t.csv", n_rows=3)
    back = pkg["resources"][0]["schema"]["fields"]
    dg.to_datapackage(back, keys, name="t", title="T", resource_path="t.csv", n_rows=3)
    dg.to_markdown(back, keys, title="T", source_file="t.csv", n_rows=3)


def test_rendered_artifacts_contain_no_numpy_reprs():
    import yaml
    df = pd.DataFrame({"age": [30.0, 40.0, 50.0, 217.0], "t": ["a", "b", "a", "b"]})
    fields = dg.profile_table(df)
    keys = dg.key_candidates(df, fields)
    md = dg.to_markdown(fields, keys, title="T", source_file="t.csv", n_rows=len(df))
    dumped = yaml.safe_dump(dg.to_datapackage(fields, keys, name="t", title="T",
                                              resource_path="t.csv", n_rows=len(df)))
    assert "np.float64" not in md and "np.int64" not in md
    assert "np.float64" not in dumped


def test_datetime_columns_are_serialisable():
    import yaml
    df = pd.DataFrame({"d": pd.to_datetime(["2023-04-11", "2023-05-12"])})
    fields = dg.profile_table(df)
    pkg = dg.to_datapackage(fields, {"primary_key_candidates": []}, name="t",
                            title="T", resource_path="t.csv", n_rows=len(df))
    yaml.safe_dump(pkg)   # must not raise RepresenterError


def test_one_sided_range_constrains_only_that_side():
    field = {"name": "v", "allowed_range": [None, 100]}
    report = dg.validate(pd.DataFrame({"v": [-500, 50, 200]}), _pkg([field]))
    detail = report[report["check"] == "range"].iloc[0]["detail"]
    assert "200" in detail and "-500" not in detail


def test_merge_flags_a_vocabulary_it_cannot_verify():
    curated = [{"name": "t", "enum_canonical": ["a"], "value_map": {"a": "a"}}]
    wide = pd.DataFrame({"t": [f"v{i}" for i in range(200)]})
    _, drift = dg.merge_curated(dg.profile_table(wide), curated)
    assert drift["unverifiable_vocabularies"], "must not report a reassuring empty diff"


def test_constant_flag_not_raised_for_a_mostly_missing_column():
    col = ["note"] * 9 + [None] * 111
    f = dg.profile_table(pd.DataFrame({"operator_notes": col}))[0]
    assert not any("single constant" in m for m in f["issues"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
