"""Run one tester job in the isolated copy and archive it (KTD2).

Safety: the runner starts `C:\\mt5r\\terminal64.exe` inside the isolated Wine prefix only. On timeout
it shuts down that prefix with `wineserver -k` under the isolated WINEPREFIX (never a name-based kill),
after re-checking that the prefix does not overlap the live one.
"""
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import time

from . import env as envmod
from . import explog

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNS = REPO / "runs"
ISO_EXE_MARKER = "C:\\mt5r\\"
MIN_FREE_GB = 5.0


@dataclasses.dataclass
class RunResult:
    run_id: str
    status: str            # ok | failed | timeout
    run_dir: pathlib.Path
    report: pathlib.Path = None
    seconds: float = 0.0


def isolated_processes() -> list:
    """Processes whose command line runs an executable from the isolated install (C:\\mt5r\\)."""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if ISO_EXE_MARKER in l]


LIVE_EXE_MARKER = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"


def live_terminal_running() -> bool:
    """True when the user's live terminal runs. On 2026-09-30 an isolated run coincided with the live
    terminal stopping ("system shutdown"), so research runs only while the live terminal is closed."""
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return any(LIVE_EXE_MARKER in l for l in out.splitlines())


def shutdown_prefix(cfg: envmod.Config) -> None:
    envmod.assert_isolated(cfg)
    subprocess.run([str(cfg.wine_dir / "bin" / "wineserver"), "-k"], env=cfg.env(), timeout=60)
    for _ in range(30):
        if not isolated_processes():
            return
        time.sleep(1)
    raise RuntimeError(f"isolated processes still alive: {isolated_processes()}")


def _free_gb(path: pathlib.Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def run(cfg: envmod.Config, run_id: str, ini_text: str, expert_ex5: str, timeout: int = 7200,
        meta: dict = None) -> RunResult:
    """Execute one /config job. `expert_ex5` is the ex5 file name under MQL5/Experts."""
    if _free_gb(cfg.mt5_dir) < MIN_FREE_GB:
        raise RuntimeError("less than 5 GB free disk; aborting before run")
    if live_terminal_running():
        raise RuntimeError("live MT5 terminal is running; research runs only while it is closed")
    busy = isolated_processes()
    if busy:
        raise RuntimeError(f"isolated terminal already running: {busy}")
    run_dir = RUNS / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    ini_rel = pathlib.Path("runs_ini") / f"{run_id}.ini"
    envmod.write_utf16(cfg.mt5_dir / ini_rel, ini_text)
    (run_dir / "tester.ini").write_text(ini_text)
    (cfg.mt5_dir / "reports").mkdir(exist_ok=True)
    for old in (cfg.mt5_dir / "reports").glob(f"{run_id}*"):
        old.unlink()
    ex5 = cfg.mt5_dir / "MQL5" / "Experts" / expert_ex5
    manifest = {"run_id": run_id, "ex5": expert_ex5, "ex5_sha256": _sha(ex5), "ini_sha256":
                hashlib.sha256(ini_text.encode()).hexdigest(), "meta": meta or {},
                "started": dt.datetime.now().isoformat(timespec="seconds")}
    cmd = [str(cfg.wine_dir / "bin" / "wine64"), "C:\\mt5r\\terminal64.exe", "/portable",
           f"/config:C:\\mt5r\\{str(ini_rel).replace('/', chr(92))}"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, env=cfg.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status = "ok"
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "timeout"
        shutdown_prefix(cfg)
    # the terminal may hand work to child processes; wait until nothing of the isolated install runs
    deadline = time.time() + 120
    while isolated_processes() and time.time() < deadline:
        time.sleep(2)
    seconds = round(time.time() - t0, 1)
    report = _collect(cfg, run_id, run_dir, t0)
    if status == "ok" and report is None:
        status = "failed"
    manifest.update({"status": status, "seconds": seconds, "report": report.name if report else None,
                     "exit_code": proc.returncode})
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return RunResult(run_id, status, run_dir, report, seconds)


def _collect(cfg, run_id, run_dir, t0):
    report = None
    for f in sorted((cfg.mt5_dir / "reports").glob(f"{run_id}*")):
        shutil.copy2(f, run_dir / f.name)
        if f.suffix in (".htm", ".html", ".xml") and ".forward" not in f.name and report is None:
            report = run_dir / f.name
    for f in cfg.mt5_dir.rglob(f"rl_*_{run_id}.csv"):
        shutil.copy2(f, run_dir / f.name)
    logs = run_dir / "logs"
    logs.mkdir()
    for f in list(cfg.mt5_dir.glob("logs/*.log")) + list(cfg.mt5_dir.glob("Tester/**/logs/*.log")):
        if f.stat().st_mtime >= t0 - 5:
            rel = f.relative_to(cfg.mt5_dir).as_posix().replace("/", "__")
            shutil.copy2(f, logs / rel)
    return report
