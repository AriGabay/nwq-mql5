# CLAUDE.md — working rules for nwq-mql5

Reply to the user in Hebrew. Project state and next step: `docs/PROJECT_STATUS.md`. Terms: `CONCEPTS.md`.

## Sync to GitHub (standing instruction from the user; PR and merge rule updated 2026-10-04)

The official repository is `https://github.com/AriGabay/nwq-mql5.git` (remote `origin`). It is **public**.

After every significant unit of work that is complete and verified — a change to the EA, the research pipeline or
the protocol, a significant fix, or a finished research stage:

1. Review the diff and run the verification that fits the change (at least `python -m pytest research/tests -q`
   for pipeline code; compile and tester checks for the EA).
2. Update `docs/PROJECT_STATUS.md` when the state, results or next step changed.
3. Make a focused commit and push it to the working branch.
4. Open a PR to `main`, or update the open one. Once the required checks have passed, merge it yourself with a
   **merge commit** (`gh pr merge --merge`), which keeps every commit for protocol traceability. Never squash or
   rebase-merge.
5. Confirm the change is on GitHub: `git fetch origin`, then compare the branch SHA and check that the merged
   commit is in `origin/main`. If a push, PR or merge fails, say explicitly that the work is **not yet synced** or
   **not merged**. Never report a merge that did not happen.
6. Report: what changed and why, what was and was not verified, the branch name, the full SHA pushed, the PR link,
   the merge status, the full SHA of `main` after the merge, and links to the commit and the main files
   (`https://github.com/AriGabay/nwq-mql5/commit/<sha>`).

The user has approved these pushes, PRs and merges in advance as part of the work they request. Do not ask again for
each regular push or merge.

**Never:**
- force-push;
- bypass checks or branch protections (no `--admin` merge, no disabling a rule).

**When something is missing:**
- If `gh` is missing, use an authorized GitHub tool, or install `gh`. Its absence alone is no reason to leave
  verified work without a PR.
- If permission or sign-in is missing, report that blocker explicitly.

## Public repository: never commit

- passwords, tokens, account numbers, login files, `*.dat`, `accounts.*`;
- `research/config.yaml` (local machine paths and the account number; gitignored) or any local MT5 configuration;
- raw tester runs (`runs/`, gitignored) or tick history. Large data stays outside git; record where it lives
  (`docs/PROJECT_STATUS.md`, data section).

Before every commit, scan the staged diff for the account number from `research/config.yaml` without printing it.
The user's untracked file `mql5/Experts/ob_fvg_retest copy.mq5` is theirs: never stage, edit or delete it.

## Evidence and protocol

- Keep earlier evidence; never overwrite or delete old results with new ones. New runs get new folders; finished
  research moves to `archive/`.
- Parameters may be selected on train data only, by a selection rule frozen in a committed pre-registration before
  the runs. Out-of-window (OOS) results, robustness runs and the August–September check may never change a choice
  or a rule. A trading-rule change, a new research, or widening the grid needs explicit approval from the user.
  Never produce `recommended.set` without an independent validation period.
- A frozen protocol (`research/preregistration.json`, `research/preregistration_numeric_v1.json`) does not change
  after its commit.

## MT5 safety

- Work only in the MT5 Strategy Tester through the isolated runner (`research/mt5r/runner.py`). Simulated trades
  inside the isolated Strategy Tester are allowed. Never open, modify or close trades on any connected account (live
  or demo).
- Never start, stop, reconfigure or attach to the live terminal. Research runs only while it is closed; if it is
  open, ask the user to close it. Never close it yourself.
- **One limited exception (user approval, 2026-10-05):** for the Dukascopy data check only, the isolated copy
  (`C:\mt5r`, `/portable`) may be started outside the Tester to run one import script that creates a custom symbol and
  loads the January 2024 and March 2026 Dukascopy ticks. Conditions:
  - account connection blocked, AutoTrading off;
  - the script contains no trading functions;
  - `ShutdownTerminal=1`;
  - the live terminal closed and untouched.

  This permits no trading and no other action outside the Tester; all checks after the import run in the Tester.
