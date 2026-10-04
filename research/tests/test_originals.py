import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "archive" / "2026-09-30-new-test-sweepob"


def test_runs_and_local_config_are_ignored():
    for path in ["runs/x/report.htm", "archive/2026-09-30-new-test-sweepob/runs/x/report.htm",
                 "research/config.yaml", "config/accounts.dat"]:
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert r.returncode == 0, path


def test_archive_keeps_old_evidence():
    for rel in ["original/SHA256SUMS", "original/new_test_v1.03.mq5", "mql5/Experts/new_test.mq5",
                "results/experiment_log.jsonl", "results/independence_check.md", "README.md"]:
        assert (ARCHIVE / rel).is_file(), rel
