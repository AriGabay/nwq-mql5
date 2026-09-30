from mt5r import deliver


def _acc(passed, recommended):
    return {"recommended": recommended, "acceptance": {"_passed_all": passed}}


def test_recommended_requires_all_criteria_and_independent_validation():
    # Covers R28 / AE3: R27 passing is not enough without independent validation
    assert deliver.should_write_recommended(_acc(True, True))
    assert not deliver.should_write_recommended(_acc(True, False))
    assert not deliver.should_write_recommended(_acc(False, True))
    assert not deliver.should_write_recommended({"acceptance": {"_passed_all": True}})


def test_parameter_table_lists_all_optimized_inputs_with_ranges():
    d = {"final": {"params": {"PivL": 2, "PivR": 3, "SweepToSetupBars": 24, "VolumeMultiplier": 1.5, "ConfirmationBars": 6}},
         "prereg": {"grid": {"PivL": [2, 3, 4, 5], "PivR": [2, 3, 4], "SweepToSetupBars": [6, 12, 18, 24],
                             "VolumeMultiplier": [1.2, 1.5, 2.0, 2.5], "ConfirmationBars": [3, 6, 9]}}}
    t = deliver.parameter_table(d)
    for p in deliver.PARAMS:
        assert f"| `{p}` |" in t
    assert "| `PivL` | 3 | 2 | 2, 3, 4, 5 |" in t
    assert "| `PivR` | 3 | 3 | 2, 3, 4 | unchanged" in t


def test_set_file_lines_round_trip(tmp_path):
    from mt5r import setfile
    p = tmp_path / "x.set"
    setfile.write_set(p, ["A=1||1||0||1||N", "B=x"], header="h")
    assert deliver.set_file_lines(p) == ["A=1||1||0||1||N", "B=x"]
