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

`fixtures/` holds fake rows for each schema (no real Slack text, names or links). `tests/contract/` validates every fixture, checks cross-references resolve, and asserts known-bad rows are refused. Run `pip install -r requirements.txt && pytest tests/`.
