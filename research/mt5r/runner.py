"""Run one tester job in the isolated copy and archive it (KTD2).

Safety: the runner starts `C:\\mt5r\\terminal64.exe` of the isolated install only (inside its own Wine prefix,
or natively as a portable copy on Windows). On timeout it shuts down that prefix with `wineserver -k` under the
isolated WINEPREFIX, or natively ends only the PIDs running from the isolated dir (never a name-based kill),
after re-checking that the prefix does not overlap the live one.
"""
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time

from . import env as envmod
from . import textio

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNS = REPO / "runs"
ISO_EXE_MARKER = "C:\\mt5r\\"
LIVE_EXE_MARKER = "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
MIN_FREE_GB = 5.0


@dataclasses.dataclass
class RunResult:
    run_id: str
    status: str            # ok | failed | timeout
    run_dir: pathlib.Path
    report: pathlib.Path = None
    seconds: float = 0.0


# An MT5 process whose path Windows hides from us (it runs elevated): it may be the live or the isolated
# terminal, so it counts as both and blocks every run (fail closed), but is never killed.
UNREADABLE = "UNREADABLE:"
PS_WINDOWS = ("Get-CimInstance Win32_Process | ForEach-Object { if ($_.ExecutablePath) "
              "{ \"$($_.ProcessId) $($_.ExecutablePath) $($_.CommandLine)\" } "
              "elseif ($_.Name -match '^(terminal64|metatester64|metaeditor64)\\.exe$') "
              "{ \"$($_.ProcessId) " + UNREADABLE + "$($_.Name)\" } }")


def _ps_lines() -> list:
    """`<pid> <command line>` per process (Windows: `<pid> <exe path> <command line>`)."""
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_WINDOWS]
    else:
        cmd = ["ps", "-axo", "pid=,command="]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:   # fail closed: an unreadable process list must never read as "MT5 closed"
        raise RuntimeError(f"process list unavailable (exit {r.returncode}); refusing to treat MT5 as closed")
    return r.stdout.splitlines()


def _exe(line: str) -> str:
    """Everything after the PID: the executable path first (Windows), or the command line (ps)."""
    return line.strip().split(" ", 1)[-1]


def _unreadable(line: str) -> bool:
    return _exe(line).startswith(UNREADABLE)


def _is_isolated(line: str, cfg) -> bool:
    if _unreadable(line):
        return True
    if cfg is not None and cfg.native:   # the executable itself must lie in the isolated dir (child processes
        return _exe(line).lower().startswith(cfg.iso_marker.lower())   # like WebView2 only mention it in args)
    return (cfg.iso_marker if cfg else ISO_EXE_MARKER).lower() in line.lower()


def isolated_processes(cfg: envmod.Config = None) -> list:
    """Processes that run an executable from the isolated install (C:\\mt5r\\), plus unreadable MT5 ones."""
    return [l.strip() for l in _ps_lines() if _is_isolated(l, cfg)]


def live_terminal_running() -> bool:
    """True when the user's live terminal runs (or an MT5 process we cannot identify). Research runs only while
    it is closed: an isolated run under MetaTrader 5.app's Wine coincided with the live terminal stopping."""
    return any(LIVE_EXE_MARKER.lower() in l.lower() or _unreadable(l) for l in _ps_lines())


def shutdown_prefix(cfg: envmod.Config) -> None:
    """Stop only the isolated install: `wineserver -k` of its prefix, or natively by the PIDs of processes
    whose executable lies in the isolated install dir (never by process name)."""
    envmod.assert_isolated(cfg)
    if cfg.native:
        for line in (l for l in isolated_processes(cfg) if not _unreadable(l)):
            subprocess.run(["taskkill", "/PID", line.split()[0], "/T", "/F"], timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run([str(cfg.wine_dir / "bin" / "wineserver"), "-k"], env=cfg.env(), timeout=60)
    for _ in range(30):
        if not isolated_processes(cfg):
            return
        time.sleep(1)
    raise RuntimeError(f"isolated processes still alive: {isolated_processes(cfg)}")


def _free_gb(path: pathlib.Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def _sha(path: pathlib.Path) -> str:
    return textio.sha256(path) if path.exists() else None


def run(cfg: envmod.Config, run_id: str, ini_text: str, expert_ex5: str, timeout: int = 7200,
        meta: dict = None) -> RunResult:
    """Execute one /config job. `expert_ex5` is the ex5 file name under MQL5/Experts."""
    if _free_gb(cfg.mt5_dir) < MIN_FREE_GB:
        raise RuntimeError("less than 5 GB free disk; aborting before run")
    if live_terminal_running():
        raise RuntimeError("live MT5 terminal is running; research runs only while it is closed")
    busy = isolated_processes(cfg)
    if busy:
        raise RuntimeError(f"isolated terminal already running: {busy}")
    envmod.disable_mcp(cfg)   # build 6231 re-adds an empty [MCP.Custom] on every exit; the check below still decides
    envmod.assert_trade_safety(cfg)
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
    cmd = cfg.launcher() + [cfg.win_path("terminal64.exe"), "/portable", f"/config:{cfg.win_path(ini_rel.as_posix())}"]
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
    while isolated_processes(cfg) and time.time() < deadline:
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
    for pattern in (f"Tester/Agent-*/MQL5/Files/rl_*_{run_id}.csv", f"MQL5/Files/rl_*_{run_id}.csv"):
        for f in cfg.mt5_dir.glob(pattern):
            shutil.copy2(f, run_dir / f.name)
    logs = run_dir / "logs"
    logs.mkdir()
    for f in list(cfg.mt5_dir.glob("logs/*.log")) + list(cfg.mt5_dir.glob("Tester/logs/*.log")) + list(cfg.mt5_dir.glob("Tester/Agent-*/logs/*.log")):
        if f.stat().st_mtime >= t0 - 5:
            rel = f.relative_to(cfg.mt5_dir).as_posix().replace("/", "__")
            shutil.copy2(f, logs / rel)
    return report
