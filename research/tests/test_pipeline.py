import importlib
import json
import pathlib


from mt5r import explog, m1_contract, pipeline, runner

EA_SRC = """
enum ENUM_STRUCTURE_VARIANT { SV_HH_ONLY = 0, SV_HH_HL = 1 };
input ENUM_STRUCTURE_VARIANT StructureVariant = SV_HH_ONLY;
input int             ImpulseWindowBars = 2;
input int             SwingStrengthM1   = 3;
input int             StopBufferPoints  = 20;
input double          RiskRR            = 2.0;
input double          RiskPercent       = 1.0;
input int             MaxExposures      = 3;
input int             WarmupDays        = 30;
input long            MagicNumber       = 770201;
input string          TradeComment      = "OBM1";
#ifdef RESEARCH_LOG
input string          ResearchRunTag    = "";
#endif
"""


def test_import_does_not_need_preregistration(monkeypatch):
    real = pathlib.Path.read_text

    def no_prereg(self, *a, **k):
        if self.name == "preregistration.json":
            raise FileNotFoundError(self)
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", no_prereg)
    mod = importlib.reload(pipeline)
    assert mod.prereg() is None
    assert mod.RUN["symbol"] == "XAUUSD.s"
    # KTD14: the structure variant is the only optimized input; A (0) is the default and baseline
    assert mod.grid() == {"StructureVariant": [0, 1]}
    assert mod.defaults() == {"StructureVariant": 0}
    assert mod.categorical() == ["StructureVariant"]


def test_grid_comes_from_preregistration_when_present(tmp_path, monkeypatch):
    p = tmp_path / "preregistration.json"
    p.write_text(json.dumps({"grid": {"StructureVariant": [0, 1]}, "defaults": {"StructureVariant": 0},
                             "categorical": ["StructureVariant"]}))
    monkeypatch.setattr(pipeline, "PREREG_PATH", p)
    assert pipeline.params() == ["StructureVariant"]
    assert pipeline.defaults() == {"StructureVariant": 0}
    assert pipeline.categorical() == ["StructureVariant"]


def test_builds_point_at_the_m1_structure_ea():
    assert pipeline.BUILDS["delivered"][0] == "ob_m1_structure.ex5"
    assert pipeline.BUILDS["research"][0] == "ob_m1_structure_research.ex5"
    for kind, research in (("delivered", False), ("research", True)):
        assert pipeline.BUILDS[kind][1].name == m1_contract.EA_SOURCE
        assert pipeline.BUILDS[kind][2] is research


def test_base_values_are_run_constants_only():
    """The EA runs on the M1 chart and has no SignalTF input; the chart period goes to the ini only."""
    assert pipeline.base_values("research") == {"RiskPercent": 1.0, "MaxExposures": 3, "RiskRR": 2.0}


def test_run_single_builds_an_m1_ini_from_run_constants(tmp_path, monkeypatch):
    src = tmp_path / "ob_m1_structure.mq5"
    src.write_text(EA_SRC)
    monkeypatch.setitem(pipeline.BUILDS, "research", ("ob_m1_structure_research.ex5", src, True))
    seen, logged = {}, []

    def fake_run(cfg, run_id, text, ex5, timeout=0, meta=None):
        seen.update(run_id=run_id, text=text, ex5=ex5, meta=meta)
        return runner.RunResult(run_id, "failed", tmp_path, None, 1.0)

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(explog, "append", lambda e: logged.append(e))
    res, rep = pipeline.run_single(None, "pilot_a", "research", "M1", "2025.12.01", "2026.07.31",
                                   overrides={"StructureVariant": 1}, role="pilot", log_extra={"ea_sha256": "abc"})
    assert rep is None and res.status == "failed"
    lines = seen["text"].split("\r\n")
    for want in ["Expert=ob_m1_structure_research.ex5", "Symbol=XAUUSD.s", "Period=M1", "Model=4",
                 "FromDate=2025.12.01", "ToDate=2026.08.01", "Deposit=10000", "Currency=USD", "Leverage=1:100",
                 "StructureVariant=1||1||0||1||N", "RiskPercent=1.0||1.0||0||1.0||N", "MaxExposures=3||3||0||3||N",
                 "ResearchRunTag=pilot_a"]:
        assert want in lines, want
    assert not any(l.startswith("SignalTF") for l in lines)
    assert logged[0]["id"] == "pilot_a" and logged[0]["status"] == "failed"
    assert logged[0]["has_report"] is False and logged[0]["ea_sha256"] == "abc"


def test_delivered_build_has_no_research_tag(tmp_path, monkeypatch):
    src = tmp_path / "ob_m1_structure.mq5"
    src.write_text(EA_SRC)
    monkeypatch.setitem(pipeline.BUILDS, "delivered", ("ob_m1_structure.ex5", src, False))
    vals, lines, ex5 = pipeline._prepare("x", "delivered", "M1", {"StructureVariant": 1})
    assert ex5 == "ob_m1_structure.ex5"
    assert "StructureVariant=1||1||0||1||N" in lines
    assert not any(l.startswith("ResearchRunTag") for l in lines)


def test_check_inputs_loaded_float_tolerant():
    rep = {"inputs": {"RiskPercent": "1", "StructureVariant": "1"}}
    assert pipeline.check_inputs_loaded(rep, {"RiskPercent": 1.0, "StructureVariant": 1}) == []
    assert pipeline.check_inputs_loaded(rep, {"StructureVariant": 0, "Missing": 1}) == [
        ("StructureVariant", 0, "1"), ("Missing", 1, None)]
