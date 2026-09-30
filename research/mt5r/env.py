"""Isolated, credential-free MT5 copy in its own Wine prefix (KTD1).

The live MT5 data directory is read-only input: only the allowlisted files below are copied.
"""
import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]

# Paths (relative to the MT5 install dir) that may be copied from the live directory.
COPY_ALLOWLIST = [
    "terminal64.exe",
    "metatester64.exe",
    "MetaEditor64.exe",
    "MQL5/Include/**",
    "config/servers.dat",
    "Bases/{server}/symbols/symbols-*.dat",
    "Bases/{server}/symbols/selected-*.dat",
    "Bases/{server}/history/{symbol}/**",
    "Bases/{server}/ticks/{symbol}/**",
]
# Files the pipeline itself writes into the copy before the first run.
GENERATED = ["config/common.ini", "config/assistant.ini", "MQL5/Experts/*.mq5", "MQL5/Experts/*.ex5"]

CREDENTIAL_KEYS = ("password=", "certpassword=", "proxypassword=")


@dataclasses.dataclass
class Config:
    live_mt5_dir: pathlib.Path
    live_prefix: pathlib.Path
    isolated_prefix: pathlib.Path
    wine_dir: pathlib.Path
    server: str
    symbol: str
    login: int = None   # account number only (user-approved); a password is never written

    @property
    def mt5_dir(self) -> pathlib.Path:
        """Install dir of the research terminal inside the isolated prefix (C:\\mt5r)."""
        return self.isolated_prefix / "drive_c" / "mt5r"

    def env(self) -> dict:
        e = dict(os.environ)
        e["WINEPREFIX"] = str(self.isolated_prefix)
        e["WINEDEBUG"] = "-all"
        e["DYLD_FALLBACK_LIBRARY_PATH"] = f"{self.wine_dir / 'lib'}:/usr/lib"
        return e


def load_config(path=None) -> Config:
    path = pathlib.Path(path or REPO / "research" / "config.yaml")
    raw = yaml.safe_load(path.read_text())
    exp = lambda k: pathlib.Path(os.path.expanduser(raw[k])).resolve()
    cfg = Config(exp("live_mt5_dir"), exp("live_prefix"), exp("isolated_prefix"), exp("wine_dir"),
                 raw["server"], raw["symbol"], raw.get("login"))
    assert_isolated(cfg)
    return cfg


def assert_isolated(cfg: Config) -> None:
    """The research prefix must never be, contain, or sit inside the live prefix."""
    iso, live = cfg.isolated_prefix.resolve(), cfg.live_prefix.resolve()
    if iso == live or live in iso.parents or iso in live.parents:
        raise RuntimeError(f"isolated prefix {iso} overlaps the live prefix {live}")


def _patterns(cfg: Config, patterns) -> list:
    return [p.format(server=cfg.server, symbol=cfg.symbol) for p in patterns]


def _match(rel: str, patterns) -> bool:
    for p in patterns:
        if p.endswith("/**"):
            if rel.startswith(p[:-3] + "/"):
                return True
        elif fnmatch.fnmatchcase(rel, p):
            return True
    return False


def plan_copy(cfg: Config) -> list:
    """Relative paths under the live MT5 dir that match the copy allowlist."""
    pats = _patterns(cfg, COPY_ALLOWLIST)
    out = []
    for path in cfg.live_mt5_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(cfg.live_mt5_dir).as_posix()
            if _match(rel, pats):
                out.append(rel)
    return sorted(out)


def check_allowlist(cfg: Config, root: pathlib.Path = None) -> list:
    """Return violations: files in the copy outside allowlist+generated, or credential keys in ini files."""
    root = root or cfg.mt5_dir
    pats = _patterns(cfg, COPY_ALLOWLIST) + GENERATED
    bad = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if not _match(rel, pats):
            bad.append(rel)
            continue
        if rel.endswith(".ini"):
            text = _read_text(path).lower()
            for line in (l.strip() for l in text.splitlines()):
                if any(line.startswith(k) for k in CREDENTIAL_KEYS):
                    bad.append(rel + " (credential key)")
                elif line.startswith("login=") and line != f"login={cfg.login}".lower():
                    bad.append(rel + " (unapproved login)")
    return bad


def _read_text(path: pathlib.Path) -> str:
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8", errors="replace")


def common_ini(cfg: Config) -> str:
    """Minimal terminal config: server, optional account number (never a password), no news."""
    login = f"Login={cfg.login}\r\n" if cfg.login else ""
    return (
        "[Common]\r\n"
        f"Server={cfg.server}\r\n"
        f"{login}"
        "NewsEnable=0\r\n"
        "ProxyEnable=0\r\n"
        "[Experts]\r\n"
        "AllowLiveTrading=0\r\n"
        "AllowDllImport=0\r\n"
        "Enabled=0\r\n"
    )


def disable_mcp(cfg: Config) -> None:
    """Turn off the terminal's built-in MCP servers (they expose trade tools) in the isolated copy."""
    path = cfg.mt5_dir / "config" / "assistant.ini"
    text = _read_text(path) if path.exists() else ""
    text = re.sub(r"(\[MCP\.[^\]]+\]\r?\n)Enable=1", r"\1Enable=0", text)
    text = re.sub(r"ApiKey=[^\r\n]*", "ApiKey=", text)
    for section in ("MCP.MetaEditor", "MCP.MetaTrader"):
        if f"[{section}]" not in text:
            text += f"[{section}]\r\nEnable=0\r\n"
    write_utf16(path, text)


def write_utf16(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))


def build(cfg: Config) -> dict:
    """Create the prefix (if needed), copy allowlisted files, write config, verify. Returns manifest."""
    assert_isolated(cfg)
    if not (cfg.isolated_prefix / "drive_c").exists():
        subprocess.run([str(cfg.wine_dir / "bin" / "wine64"), "wineboot", "--init"], env=cfg.env(),
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
    files = plan_copy(cfg)
    for rel in files:
        src, dst = cfg.live_mt5_dir / rel, cfg.mt5_dir / rel
        if not dst.exists() or dst.stat().st_size != src.stat().st_size or dst.stat().st_mtime < src.stat().st_mtime:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    write_utf16(cfg.mt5_dir / "config" / "common.ini", common_ini(cfg))
    disable_mcp(cfg)
    bad = check_allowlist(cfg)
    if bad:
        raise RuntimeError(f"allowlist violation in isolated copy: {bad[:10]}")
    manifest = {"files": {rel: _sha(cfg.mt5_dir / rel) for rel in files if rel.endswith(".exe")},
                "n_files": len(files)}
    (cfg.isolated_prefix.parent / "env_manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def _sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install_sources(cfg: Config) -> None:
    """Copy the repo EA sources into the isolated copy's MQL5/Experts."""
    dst = cfg.mt5_dir / "MQL5" / "Experts"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ["new_test.mq5", "new_test_research.mq5"]:
        shutil.copy2(REPO / "mql5" / "Experts" / name, dst / name)
    shutil.copy2(REPO / "original" / "new_test_v1.03.mq5", dst / "new_test_v103.mq5")
