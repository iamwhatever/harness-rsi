"""Facts about the checkout exams run in, for the question setter: it has no file tools, so it guesses.

Without these the setter invents modules (``harness.tests.x``), commands and artifact files, and picks
metrics nothing measures. Everything here is read from the checkout the judge runs exams in, at its HEAD:
real module paths with their public functions, commands on PATH, the measured metrics, the UI's
``data-testid`` hooks, and up to one published exam per check kind as a worked example.
"""

from __future__ import annotations

import ast
import collections
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

COMMANDS = ("python3", "python", "git", "node", "npx", "bash", "pytest")
PER_SIGNAL, NAMES_PER_MODULE, TESTIDS_PER_SIGNAL = 5, 8, 5
LINE = 400  # chars per module line, so the message stays well under one argv string
_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_TESTID = re.compile(r"""data-testid=["'{`]*([a-z][\w-]+)""")
_STOP = set("the and for with that this from into when not but are was has have its new one all any can out "
            "via per same after before then than only still each been does kiro crew kirocrew judge passes "
            "steps fresh repeat behind around recent within works because looks like they own saw".split())


def words(text: str) -> set[str]:
    """Crude stems (first 5 letters), so "redacts" meets "redaction"."""
    return {w[:5] for w in _WORD.findall(text.lower().replace("_", " ").replace("-", " ")) if w not in _STOP}


def modules(work: Path, src: str = "src") -> dict[str, list[str]]:
    """Dotted module path -> its public top-level functions (with arguments) and classes; tests skipped."""
    out, root = {}, work / src
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        parts = path.relative_to(root).with_suffix("").parts
        if any(p == "tests" or p.startswith("test_") for p in parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue
        names = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""  # misread returns break exams
                names.append(f"{node.name}({', '.join(a.arg for a in node.args.args)}){ret}")
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                names.append(node.name)
        out[".".join(parts).removesuffix(".__init__")] = names
    return out


def testids(work: Path, src: str = "website/src") -> dict[str, str]:
    """Every ``data-testid`` in the UI source -> the first file using it."""
    out, root = {}, work / src
    for path in sorted(root.rglob("*.tsx")) if root.is_dir() else []:
        if "test" in path.parts or ".test." in path.name:
            continue
        for tid in _TESTID.findall(path.read_text(encoding="utf-8", errors="replace")):
            out.setdefault(tid, str(path.relative_to(work)))
    return out


def rank(keys: dict[str, str], want: set[str], k: int, floor: float = 0.8) -> list[str]:
    """The `k` keys best matching `want`: rare shared words weigh more, a word in the key itself double.

    A key must score `floor` times the weight of one unique word, so one common word never qualifies.
    """
    bags = {key: (words(key), words(text)) for key, text in keys.items()}
    df = collections.Counter(w for own, text in bags.values() for w in own | text)
    idf = {w: math.log(len(bags) / df[w]) for w in want if df[w]}

    def score(own: set[str], text: set[str]) -> float:
        return 2 * sum(idf.get(w, 0) for w in own & want) + sum(idf.get(w, 0) for w in (text - own) & want)
    scored = sorted(((score(*bags[key]), key) for key in bags), key=lambda t: (-t[0], t[1]))
    return [key for n, key in scored[:k] if n > 0 and n >= floor * math.log(max(len(bags), 2))]


def facts(work: Path, signals: list[dict], metrics: list[str], examples: list[dict] = ()) -> str:
    work = work.resolve()
    if not work.is_dir():  # no checkout to read: the setter gets no facts, and the dry run refuses its exams
        return ""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()
    mods, tids = modules(work), testids(work)
    mod_text = {m: " ".join(n.split("(")[0] for n in names) for m, names in mods.items()}
    lines = [f"Checkout the judge runs every exam in (cwd), at {head or 'unknown sha'}. It is the code BEFORE any fix:",
             "a good exam FAILS here while the pain is real and passes once it is fixed.",
             "Top level: " + " ".join(sorted(p.name + ("/" if p.is_dir() else "") for p in work.iterdir()
                                             if not p.name.startswith((".", "__")))),
             "Commands on PATH: " + " ".join(c for c in COMMANDS if shutil.which(c)),
             "Python: the package is NOT installed. Run product code as "
             "[\"python3\", \"-c\", \"import sys; sys.path.insert(0, 'src'); from kiro_crew.x import f; ...\"] "
             "and exit non-zero (assert / sys.exit(1)) when the behaviour is wrong. Import only modules and names "
             "listed below; an import that fails is refused.",
             "Measured metrics (metric_threshold may name ONLY these): " + " ".join(metrics),
             "SPA: " + ("built at website/dist; dom_assert uses root \"website/dist\" and must stub every /api call it "
                        "needs with route steps" if (work / "website/dist/index.html").is_file() else "not built"),
             "No exam may read a file a run would have to produce (artifacts/, logs, screenshots): those never exist.",
             "", "Per signal, the real code it most likely touches (modules listed after):"]
    used: list[str] = []
    for s in signals:
        if not s.get("testable", {}).get("ok") or s.get("dedup_of"):
            continue
        want = words(s.get("pain", "") + " " + (s["testable"].get("task") or ""))
        hit = rank(mod_text, want, PER_SIGNAL)
        used += [m for m in hit if m not in used]
        hit += [f"data-testid={t}" for t in rank({t: "" for t in tids}, want, TESTIDS_PER_SIGNAL, 0.55)]
        lines.append(f"{s['id']}: " + (", ".join(hit) or "(no close match: prefer writing no exam over guessing)"))
    lines += ["", "Modules (public names, with return types where declared):"]
    lines += [f"{m}: {', '.join(mods[m][:NAMES_PER_MODULE]) or '(no public names)'}"[:LINE] for m in used]
    if examples:
        lines += ["", "Published exams that run (worked examples of the shape, not tasks to copy):"]
        lines += [json.dumps(x["check"]) for x in examples]
    return "\n".join(lines) + "\n"


def examples(bank_hidden: Path) -> list[dict]:
    """Up to one published exam per check kind, smallest first."""
    seen, out = set(), []
    for path in sorted(bank_hidden.glob("exam_*.json"), key=lambda p: p.stat().st_size) if bank_hidden.is_dir() else []:
        x = json.loads(path.read_text(encoding="utf-8"))
        if x.get("check", {}).get("kind") not in seen:
            seen.add(x["check"]["kind"])
            out.append(x)
    return out
