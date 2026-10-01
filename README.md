# kirocrew-rsi
Harness RSI app for KiroCrew: signal list, judge, design crew, priority cards

## Interfaces

Frozen after R0. To change one, report `question` to the conductor first. All schemas are JSON Schema draft 2020-12 with `additionalProperties: false`.

| Schema | One row is | Used by |
|---|---|---|
| `schemas/signal.schema.json` | a signal-list entry: `id`, `source`, `links`, `pain`, `mentions`, `layer`, `testable`, `dedup_of` | Radar outputs (write), design crew (read) |
| `schemas/exam.schema.json` | an exam item: `layer` 1-4, `visibility` hidden/regression/retired, and a `check` of kind `exit_code`, `file_assert`, `metric_threshold` or `screenshot_diff` | question setter, judge, seed exams |
| `schemas/judge.schema.json` | a judge run: the CLI reads `input` `{pr, repo, suites, exam_ids?}` on stdin and writes `output` `{verdict, scores, paired_metrics, evidence}` on stdout | judge (write), conductor (read) |
| `schemas/proposal.schema.json` | a priority-card row: `pain`, `signal_ids`, `heat`, `mock_artifact_slug`, `cost` (at most 10 files, 300 lines), `exam_ids`, `decision` do/skip/later/null | design crew (write), priority card (read) |

Rules the schemas enforce: ids are `sig_YYYYMMDD_NNNN`, `exam_*`, `prop_*`; a testable signal names its task; a hidden exam is used in at most one round; exams above layer 1 name an origin signal.

Judging one proposal:

1. Pass the proposal's `exam_ids` in `input`: the `hidden` and `new` suites then run exactly those exams, and an unknown id is a setup error (exit 2).
2. Measure hard metrics per run with `python3 baseline/metrics.py --out FILE`, a few times on the base and a few on the PR head.
3. Turn them into the judge's metrics file: `python -m judge.metrics_in --before b*.json --after a*.json --out metrics.json`.
4. Run `python -m judge --exams DIR --workdir DIR --metrics metrics.json < input.json`.
5. Read `evidence`: one row per exam run, with `status` `pass`, `fail` or `error` and the reason. `error` means the check could not run (missing file, tool or samples). A requested suite that ran 0 exams, or a metrics suite with no metrics file, gets its own `suite` row. Any `error` fails the verdict.

`fixtures/` holds fake rows for each schema (no real Slack text, names or links). `tests/contract/` validates every fixture, checks cross-references resolve, and asserts known-bad rows are refused. Run `pip install -r requirements.txt && pytest tests/`.
