import importlib
import json
import pathlib


from mt5r import explog, pipeline, runner

EA_SRC = """
enum ENUM_OB_MODE    { OB_FVG = 0, OB_FVG_BOS = 1 };
enum ENUM_ENTRY_MODE { ENTRY_FVG_EDGE = 0, ENTRY_FVG_MID = 1, ENTRY_OB_EDGE = 2, ENTRY_OB_MID = 3 };
input ENUM_TIMEFRAMES SignalTF          = PERIOD_M15;
input ENUM_OB_MODE    ObMode            = OB_FVG;
input ENUM_ENTRY_MODE EntryMode         = ENTRY_FVG_EDGE;
input int             ObMaxAgeBars      = 96;
input int             FvgWindowBars     = 12;
input int             OrderExpiryBars   = 12;
input double          RiskRR            = 2.0;
input double          RiskPercent       = 1.0;
input int             MaxExposures      = 3;
input string          TradeComment      = "OBR";
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
    # KTD12 grid and defaults apply until the pre-registration exists
    assert mod.grid() == {"ObMode": [0, 1], "EntryMode": [0, 1, 2, 3], "ObMaxAgeBars": [48, 96, 144],
                          "FvgWindowBars": [6, 12, 18], "OrderExpiryBars": [6, 12, 18]}
    assert mod.defaults() == {"ObMode": 0, "EntryMode": 0, "ObMaxAgeBars": 96, "FvgWindowBars": 12,
                              "OrderExpiryBars": 12}
    assert mod.categorical() == ["ObMode", "EntryMode"]


def test_grid_comes_from_preregistration_when_present(tmp_path, monkeypatch):
    p = tmp_path / "preregistration.json"
    p.write_text(json.dumps({"grid": {"ObMode": [0, 1], "ObMaxAgeBars": [48, 96]},
                             "defaults": {"ObMode": 0, "ObMaxAgeBars": 96}, "categorical": ["ObMode"]}))
    monkeypatch.setattr(pipeline, "PREREG_PATH", p)
    assert pipeline.params() == ["ObMode", "ObMaxAgeBars"]
    assert pipeline.defaults() == {"ObMode": 0, "ObMaxAgeBars": 96}
    assert pipeline.categorical() == ["ObMode"]


def test_builds_point_at_new_ea():
    assert pipeline.BUILDS["delivered"][0] == "ob_fvg_retest.ex5"
    assert pipeline.BUILDS["research"][0] == "ob_fvg_retest_research.ex5"
    for kind, research in (("delivered", False), ("research", True)):
        assert pipeline.BUILDS[kind][1].name == "ob_fvg_retest.mq5"
        assert pipeline.BUILDS[kind][2] is research


def test_base_values_are_run_constants_plus_signal_tf():
    assert pipeline.base_values("research", 5) == {"RiskPercent": 1.0, "MaxExposures": 3, "RiskRR": 2.0,
                                                   "SignalTF": 5}


def test_run_single_builds_ini_from_run_constants(tmp_path, monkeypatch):
    src = tmp_path / "ob_fvg_retest.mq5"
    src.write_text(EA_SRC)
    monkeypatch.setitem(pipeline.BUILDS, "research", ("ob_fvg_retest_research.ex5", src, True))
    seen, logged = {}, []

    def fake_run(cfg, run_id, text, ex5, timeout=0, meta=None):
        seen.update(run_id=run_id, text=text, ex5=ex5, meta=meta)
        return runner.RunResult(run_id, "failed", tmp_path, None, 1.0)

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(explog, "append", lambda e: logged.append(e))
    res, rep = pipeline.run_single(None, "pilot_m5", "research", "M5", "2025.12.01", "2026.07.31",
                                   role="pilot", log_extra={"ea_sha256": "abc"})
    assert rep is None and res.status == "failed"
    lines = seen["text"].split("\r\n")
    for want in ["Expert=ob_fvg_retest_research.ex5", "Symbol=XAUUSD.s", "Period=M5", "Model=4",
                 "FromDate=2025.12.01", "ToDate=2026.08.01", "Deposit=10000", "Currency=USD", "Leverage=1:100",
                 "SignalTF=5||5||0||5||N", "RiskPercent=1.0||1.0||0||1.0||N", "MaxExposures=3||3||0||3||N",
                 "ObMode=0||0||0||0||N", "ResearchRunTag=pilot_m5"]:
        assert want in lines, want
    assert logged[0]["id"] == "pilot_m5" and logged[0]["status"] == "failed"
    assert logged[0]["has_report"] is False and logged[0]["ea_sha256"] == "abc"


def test_delivered_build_has_no_research_tag(tmp_path, monkeypatch):
    src = tmp_path / "ob_fvg_retest.mq5"
    src.write_text(EA_SRC)
    monkeypatch.setitem(pipeline.BUILDS, "delivered", ("ob_fvg_retest.ex5", src, False))
    vals, lines, ex5 = pipeline._prepare("x", "delivered", "M15", {"EntryMode": 2})
    assert ex5 == "ob_fvg_retest.ex5"
    assert "EntryMode=2||2||0||2||N" in lines
    assert not any(l.startswith("ResearchRunTag") for l in lines)


def test_check_inputs_loaded_float_tolerant():
    rep = {"inputs": {"RiskPercent": "1", "EntryMode": "2"}}
    assert pipeline.check_inputs_loaded(rep, {"RiskPercent": 1.0, "EntryMode": 2}) == []
    assert pipeline.check_inputs_loaded(rep, {"EntryMode": 3, "Missing": 1}) == [("EntryMode", 3, "2"),
                                                                                  ("Missing", 1, None)]
