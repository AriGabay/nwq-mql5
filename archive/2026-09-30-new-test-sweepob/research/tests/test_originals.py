import hashlib
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_original_checksums_match():
    lines = (ROOT / "original" / "SHA256SUMS").read_text().split("\n")
    entries = [l.split() for l in lines if l.strip()]
    assert len(entries) == 2
    for digest, name in entries:
        actual = hashlib.sha256((ROOT / "original" / name).read_bytes()).hexdigest()
        assert actual == digest, name


def test_runs_and_local_config_are_ignored():
    for path in ["runs/x/report.htm", "research/config.yaml", "config/accounts.dat"]:
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, path
