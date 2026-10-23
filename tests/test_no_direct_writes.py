"""Increment 3: no code path may write to Sheets, Slack or committed data files
except through the guarded interfaces.

Static checks over src/ and scripts/ (review-only tools excluded):
  * ``requests.post(`` appears only in src/faramir/slack_post.py, and every
    function there that calls it also calls ``authorize``;
  * gspread write methods appear only in src/faramir/sheet.py, and every
    function there that calls one also calls ``authorize``;
  * in scripts/build_corpus.py every function that opens a file for writing
    also calls ``authorize`` (repo writes).
"""

import ast
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
REVIEW_TOOLS = {"scripts/compare_candidates.py", "scripts/verify_corpus_v2.py"}
SHEET_WRITE_METHODS = {"update", "append_row", "append_rows", "batch_update", "batch_clear", "clear", "add_worksheet"}


def sources():
    for base in ("src", "scripts"):
        for dirpath, _, files in os.walk(os.path.join(ROOT, base)):
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(dirpath, name)
                    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                    if rel not in REVIEW_TOOLS:
                        yield rel, open(path, encoding="utf-8").read()


def calls_in(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            yield n


def attr_name(call):
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)


def test_requests_post_only_in_slack_post():
    offenders = [rel for rel, src in sources()
                 if re.search(r"\brequests\.post\(", src) and rel != "src/faramir/slack_post.py"]
    assert offenders == []


def test_gspread_writes_only_in_sheet_module():
    pattern = re.compile(r"\b(ws|worksheet|sh)\.(" + "|".join(SHEET_WRITE_METHODS) + r")\(")
    offenders = [rel for rel, src in sources() if pattern.search(src) and rel != "src/faramir/sheet.py"]
    assert offenders == []


def _functions(path):
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def test_every_sheet_write_function_is_guarded():
    unguarded = []
    for fn in _functions("src/faramir/sheet.py"):
        names = {attr_name(c) for c in calls_in(fn)}
        writes = {c for c in calls_in(fn) if isinstance(c.func, ast.Attribute)
                  and c.func.attr in SHEET_WRITE_METHODS and getattr(c.func.value, "id", "") in {"ws", "sh"}}
        if writes and "authorize" not in names and fn.name != "_ensure_usage_tab":
            unguarded.append(fn.name)
    assert unguarded == []


def test_usage_tab_creation_only_reachable_from_guarded_flush():
    callers = [fn.name for fn in _functions("src/faramir/sheet.py")
               if "_ensure_usage_tab" in {attr_name(c) for c in calls_in(fn)}]
    assert callers == ["flush_usage_to_sheet"]


def test_every_slack_post_is_guarded():
    unguarded = []
    for fn in _functions("src/faramir/slack_post.py"):
        names = {attr_name(c) for c in calls_in(fn)}
        if "post" in names and any(isinstance(c.func, ast.Attribute) and getattr(c.func.value, "id", "") == "requests"
                                   for c in calls_in(fn)) and "authorize" not in names:
            unguarded.append(fn.name)
    assert unguarded == []


def test_every_repo_file_write_in_build_corpus_is_guarded():
    unguarded = []
    for fn in _functions("scripts/build_corpus.py"):
        writes_file = any(
            attr_name(c) == "open" and len(c.args) > 1 and isinstance(c.args[1], ast.Constant) and "w" in c.args[1].value
            for c in calls_in(fn)
        )
        if writes_file and "authorize" not in {attr_name(c) for c in calls_in(fn)}:
            unguarded.append(fn.name)
    assert unguarded == []
