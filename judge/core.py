"""Exam loading, the four check kinds, paired metrics and the verdict."""

import hashlib
import json
import math
import pathlib
import statistics
import subprocess

# A pair is read together: neither metric may get worse beyond its noise band, whatever the other did.
DEFAULT_PAIRS = [("first_token_ms", "success_rate"), ("tokens_per_turn", "task_completion_rate")]
LOWER_IS_BETTER = {"first_token_ms", "tokens_per_turn", "total_latency_ms", "error_rate"}
_OPS = {"lt": lambda x, v: x < v, "le": lambda x, v: x <= v, "gt": lambda x, v: x > v, "ge": lambda x, v: x >= v}


class JudgeError(Exception):
    """A setup problem (missing exam dir, bad input) rather than a failed exam."""


def load_exams(exams_dir):
    root, exams = pathlib.Path(exams_dir), []
    if not root.is_dir():
        raise JudgeError(f"exams dir not found: {root} (pass --exams or set HARNESS_RSI_DATA)")
    for path in sorted(root.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise JudgeError(f"cannot read exam file {path.name}: {exc}") from exc
        exams.extend(doc if isinstance(doc, list) else [doc])
    if len({e.get("id") for e in exams}) != len(exams):
        raise JudgeError("duplicate exam id in exams dir")
    return exams


def suites_of(exam, round_no):
    if exam["visibility"] == "retired":
        return set()
    out = {"metrics"} if exam["layer"] == 1 else {exam["visibility"]}
    return out | {"new"} if round_no is not None and exam["created_round"] == round_no else out


def stat(samples, name):
    if name in ("mean", "rate"):
        return statistics.fmean(samples)
    ranked = sorted(samples)  # nearest-rank percentile, as in baseline/metrics.json
    return ranked[max(0, math.ceil((50 if name == "p50" else 95) / 100 * len(ranked)) - 1)]


def run_check(check, ctx):
    kind, work = check["kind"], ctx["workdir"]
    if kind == "exit_code":
        timeout = check.get("timeout_s", 600)
        try:
            code = subprocess.run(check["cmd"], cwd=work, capture_output=True, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
        except OSError as exc:
            return False, f"cannot run command: {exc.strerror}"
        return code == check["expect"], f"exit {code}, expected {check['expect']}"
    if kind == "file_assert":
        path = work / check["path"]
        if not path.is_file():
            return False, f"missing file {check['path']}"
        data = path.read_bytes()
        if "sha256" in check and hashlib.sha256(data).hexdigest() != check["sha256"]:
            return False, f"sha256 mismatch on {check['path']}"
        if "contains" in check and check["contains"] not in data.decode("utf-8", "replace"):
            return False, f"{check['path']} lacks expected text"
        return True, f"{check['path']} matches"
    if kind == "metric_threshold":
        samples = ctx["metrics"].get("current", {}).get(check["metric"], [])
        if len(samples) < check["runs"]:
            return False, f"{check['metric']}: {len(samples)} runs, need {check['runs']}"
        got = stat(samples, check["stat"])
        return _OPS[check["op"]](got, check["value"]), f"{check['metric']} {check['stat']}={got:g} {check['op']} {check['value']:g}"
    base = work / check["baseline"]
    shot = ctx["shots"] / base.name
    if not base.is_file() or not shot.is_file():
        return False, f"missing image {base.name if not base.is_file() else 'shots/' + shot.name}"
    try:
        ratio = ctx["comparer"](base, shot)
    except Exception as exc:  # a pluggable comparer's failure fails the exam, never the run
        return False, f"cannot compare images: {exc}"
    return ratio <= check["max_diff_ratio"], f"diff ratio {ratio:.4f}, max {check['max_diff_ratio']}"


def paired(metrics):
    cur, base, floor = metrics.get("current", {}), metrics.get("baseline", {}), metrics.get("noise_floor", {})
    rows = []
    for a, b in (tuple(p) for p in metrics.get("pairs", DEFAULT_PAIRS)):
        if not all(cur.get(m) and base.get(m) for m in (a, b)):
            continue
        delta, loss = {}, False
        for m in (a, b):
            delta[m] = statistics.fmean(cur[m]) - statistics.fmean(base[m])
            band = max(2 * statistics.pstdev(base[m]), floor.get(m, 0.0))  # inside the band = no move
            loss = loss or ((delta[m] > band) if m in LOWER_IS_BETTER else (delta[m] < -band))
        rows.append({"a": a, "b": b, "delta_a": round(delta[a], 6), "delta_b": round(delta[b], 6), "ok": not loss})
    return rows


def judge(inp, exams, ctx):
    evidence, scores, seen = [], {}, {}
    for suite in inp["suites"]:
        results = []
        for exam in (e for e in exams if suite in suites_of(e, ctx["round"])):
            if exam["id"] not in seen:
                used = exam["used_rounds"]
                if exam["visibility"] == "hidden" and used and used != [ctx["round"]]:
                    seen[exam["id"]] = (False, f"refused: hidden exam already used in round {used[0]}")
                else:
                    seen[exam["id"]] = run_check(exam["check"], ctx)
                evidence.append({"exam_id": exam["id"], "ok": seen[exam["id"]][0], "detail": seen[exam["id"]][1]})
            results.append(seen[exam["id"]][0])
        scores[suite] = sum(results) / len(results) if results else 0.0  # an empty suite proves nothing
    pairs = paired(ctx["metrics"]) if "metrics" in inp["suites"] else []
    passed = all(v == 1.0 for v in scores.values()) and all(p["ok"] for p in pairs)
    return {"verdict": "pass" if passed else "fail", "scores": scores, "paired_metrics": pairs, "evidence": evidence}
