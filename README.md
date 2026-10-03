# kirocrew-rsi
Harness RSI app for KiroCrew: signal list, judge, design crew, priority cards

## Interfaces

Frozen after R0. To change one, report `question` to the conductor first. All schemas are JSON Schema draft 2020-12 with `additionalProperties: false`.

| Schema | One row is | Used by |
|---|---|---|
| `schemas/signal.schema.json` | a signal-list entry: `id`, `source`, `links`, `pain`, `mentions`, `layer`, `testable`, `dedup_of` | Radar outputs (write), design crew (read) |
| `schemas/exam.schema.json` | an exam item: `layer` 1-4, `visibility` hidden/regression/retired, and a `check` of kind `exit_code`, `file_assert`, `metric_threshold`, `screenshot_diff` or `dom_assert` (a CSS selector's text in the built SPA, via Playwright); `python -m judge.validate EXAM.json --workdir DIR` refuses an exam whose check cannot run, and the question setter (`crew/run_round.py --exam-workdir DIR`) writes only exams it accepts | question setter, judge, seed exams |
| `schemas/judge.schema.json` | a judge run: the CLI reads `input` `{pr, repo, suites}` on stdin and writes `output` `{verdict, scores, paired_metrics, evidence}` on stdout | judge (write), conductor (read) |
| `schemas/proposal.schema.json` | a priority-card row: `pain`, `signal_ids`, `heat`, `mock_artifact_slug`, `cost` (at most 10 files, 300 lines), `exam_ids`, `decision` do/skip/later/null | design crew (write), priority card (read) |

Rules the schemas enforce: ids are `sig_YYYYMMDD_NNNN`, `exam_*`, `prop_*`; a testable signal names its task; a hidden exam is used in at most one round; exams above layer 1 name an origin signal.

Judging one proposal:

1. Add the proposal's `exam_ids` to `input` (optional field): the `hidden` and `new` suites then run exactly those exams, and an unknown id is a setup error (exit 2).
2. Measure hard metrics per run with `python3 baseline/metrics.py --out FILE`, a few times on the base and a few on the PR head.
3. Turn them into the judge's metrics file: `python -m judge.metrics_in --before b*.json --after a*.json --out metrics.json`.
4. Run `python -m judge --exams DIR --workdir DIR --metrics metrics.json < input.json`.
5. Read `evidence`: one row per exam run, with `status` `pass`, `fail` or `error` and the reason. `error` means the check could not run (missing file, tool or samples). A requested suite that ran 0 exams, or a metrics suite with no metrics file, gets its own `suite` row. Any `error` fails the verdict.

Keeping the exam store honest (exams stay in `$HARNESS_RSI_DATA/exams`, never in git):

- `python -m judge.audit --workdir KC [--fix]` dry-runs every exam against a KiroCrew checkout `KC` with the SPA built, and prints counts: runnable, kept (held back only by this machine: no browser, no SPA, no metric samples) and rejected by reason. `--fix` moves each rejected exam to `exams/rejected/` with its reason in `rejected/reasons.jsonl`, and rewrites a `screenshot_diff` exam as `dom_assert` when its task names one selector and one quoted label. Nothing is deleted.
- `python -m judge.regress promote EXAM_ID [--used-round N]` makes a judged hidden exam a regression exam (`--used-round` records the round when none was written). `python -m judge.regress --since-last --kirocrew KC --build "cd website && npm ci && npm run build"` fetches KiroCrew main and runs the regression suite once when main has a new head since the last stored run, else reports `no new merge since last regress`. An exam that could not run is listed under `errors`, never as a regression. Hook entry for the always-on loop (no schedule yet): call that command and read exit 0 clean, 1 regression, 2 error.

Closing the loop (outcome ledger):

- `$HARNESS_RSI_DATA/outcomes.jsonl` holds one `schemas/outcome.schema.json` row per card and product PR: the decision, the PR, its merge sha, the judge's base and head runs, the exams promoted on merge and every post-merge regress result for those exams. Rows are appended whole; the newest per `(card_id, pr)` is current. `GET /outcomes` reads it and each card shows its PRs' scores; `POST /outcomes/link {proposal_id, pr}` links a card to a KiroCrew PR from the board.
- `python -m backend.autoscore --kirocrew KC` reads the 50 newest KiroCrew PRs and every linked one in one GraphQL call (read-only, stops below 200 API points), links a PR whose title or body names a card id, and when a linked PR's head moved runs the card's exams (its `exam_ids` plus exams written for its signals) on the base and on the head in throwaway worktrees. On merge it promotes those exams to regression and runs the regression suite on the merge commit. Fork PRs are linked but never run. Paired metrics come from `$HARNESS_RSI_DATA/metrics/<owner>__<repo>__<n>.json` (`judge.metrics_in` output) when one exists. The board's Score PRs button (`POST /score/run`) starts it as a single-flight job, and the hourly tick does when "Score linked KiroCrew PRs" is on in Settings.
- Back-fill: `python -m backend.autoscore --kirocrew KC --pr N [--card prop_x]` scores one PR now; a PR with no runnable exam or no card says so in `note`.

Improving the crew's own prompts:

- Each crew prompt has a version id (`crew/prompts.py`: `v` + 10 hex digits of the text's sha256). A round writes the versions it ran to `$HARNESS_RSI_DATA/prompt_versions.json` and prints them. A prompt change the owner applied lives in `$HARNESS_RSI_DATA/prompts/<agent>.md` and wins over the repo copy; each apply appends to `prompt_versions.jsonl`.
- `python3 crew/ab.py --agent AGENT --variant B.md --kirocrew KC [--rounds 3 4 5] [--reps 3]` replays the saved rounds in `$HARNESS_RSI_DATA/rounds/round-N` with the current prompt (A) and the variant (B), and gives each hard metric A, B and a verdict (better / same / worse, the judge's paired noise band read both ways): setter `setter_hit_rate` (exams that run, fail on a merged fix PR's base and pass on its head), reviewers `adopt_rate` and `prior_art_fp_rate`, and `credits_per_turn`. Replies are cached by prompt version under `$HARNESS_RSI_DATA/ab/`.
- Hidden exams never go into a prompt: a variant or applied prompt naming a hidden exam's id, task or check is refused, the setter's checkout facts show only regression (already judged) exams as worked examples, and `tests/contract/test_no_exam_leak.py` checks every agent message of a round and the facts block.

`fixtures/` holds fake rows for each schema (no real Slack text, names or links). `tests/contract/` validates every fixture, checks cross-references resolve, and asserts known-bad rows are refused. Run `pip install -r requirements.txt && pytest tests/`.
