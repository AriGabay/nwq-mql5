"""Compile EA sources with MetaEditor's command line inside the isolated copy (KTD3)."""
import pathlib
import re
import subprocess

from . import env as envmod
from .textio import read_text
from .runner import isolated_processes


def compile_ea(cfg: envmod.Config, source_name: str, timeout: int = 600, folder: str = "Experts") -> dict:
    """Compile MQL5/<folder>/<source_name> (Experts by default); return errors/warnings counts and the log text."""
    if isolated_processes(cfg):
        raise RuntimeError("isolated terminal busy")
    log_rel = f"compile_{pathlib.Path(source_name).stem}.log"
    log = cfg.mt5_dir / log_rel
    if log.exists():
        log.unlink()
    cmd = cfg.launcher() + [cfg.win_path("MetaEditor64.exe"), "/portable",
                            f"/compile:{cfg.win_path(f'MQL5/{folder}/' + source_name)}", f"/log:{cfg.win_path(log_rel)}"]
    subprocess.run(cmd, env=cfg.env(), timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    text = read_text(log, errors="replace") if log.exists() else ""
    m = re.search(r"(\d+)\s+errors?,\s*(\d+)\s+warnings?", text)
    errors, warnings = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    ex5 = cfg.mt5_dir / "MQL5" / folder / (pathlib.Path(source_name).stem + ".ex5")
    return {"source": source_name, "errors": errors, "warnings": warnings, "ex5_exists": ex5.exists(), "log": text}
