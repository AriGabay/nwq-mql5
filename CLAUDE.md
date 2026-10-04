# CLAUDE.md — working rules for nwq-mql5

Reply to the user in Hebrew. Project state and next step: `docs/PROJECT_STATUS.md`. Terms: `CONCEPTS.md`.

## Sync to GitHub (standing instruction from the user, 2026-10-04)

The official repository is `https://github.com/AriGabay/nwq-mql5.git` (remote `origin`). It is **public**.

After every significant unit of work that is complete and verified — a change to the EA, the research pipeline or
the protocol, a significant fix, or a finished research stage:

1. Review the diff and run the verification that fits the change (at least `python -m pytest research/tests -q`
   for pipeline code; compile and tester checks for the EA).
2. Update `docs/PROJECT_STATUS.md` when the state, results or next step changed.
3. Make a focused commit and push it to the working branch. The user has approved these pushes as part of the work
   they request; do not ask again for each regular push.
4. Confirm the local commit is on GitHub (`git fetch origin` and compare SHAs). If a push fails, say explicitly that
   the work is **not yet synced**.
5. Report: what changed and why, what was and was not verified, the branch name, the full SHA pushed, and links to
   the commit and the main files (`https://github.com/AriGabay/nwq-mql5/commit/<sha>`).

Never force-push, and never merge into `main` as part of syncing; merges go through a PR the user handles.

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
- Do not choose rules or parameters by P&L. A trading-rule change, a new research, or widening the grid needs
  explicit approval from the user. Never produce `recommended.set` without an independent validation period.
- The frozen protocol (`research/preregistration.json`) does not change after its commit.

## MT5 safety

- Work only in the MT5 Strategy Tester through the isolated runner (`research/mt5r/runner.py`). Never open, modify
  or close trades on any account.
- Never start, stop, reconfigure or attach to the live terminal. Research runs only while it is closed; if it is
  open, ask the user to close it. Never close it yourself.
