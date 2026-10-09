"""engine-boundary S3-reproof-outputs (AC 14, "api_key gone from the engine"): the whole
clause in one file -- the live signature, an AST scan of every `pdft_engine` module (a
glob, not a hand-maintained list, so a file added after this test is written is still
covered -- `test_engine_signature.py`'s own `ENGINE_FILES` tuple predates
`callback_client.py` and `server.py`, S3-engine-server/S3-app-transport; this file never
goes stale that way), and a lock scan proving no provider SDK ever entered the engine's
own dependency closure."""
from __future__ import annotations

import ast
import inspect
import tomllib
from pathlib import Path

from pdft_engine import pipeline

ENGINE_DIR = Path(__file__).resolve().parents[1] / "pdft_engine"
LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
FORBIDDEN_PARAMS = {"api_key", "translator", "provider", "model", "attempt_controller"}
FORBIDDEN_PACKAGES = {"anthropic", "openai", "google-genai"}


def _engine_files() -> list[Path]:
    return sorted(ENGINE_DIR.rglob("*.py"))


def test_engine_has_python_files_to_scan():
    """Non-vacuity floor: an empty glob would make the AST check below pass on
    nothing, silently."""
    assert len(_engine_files()) >= 10, _engine_files()


def test_run_pipeline_signature_carries_no_key_provider_or_translator():
    params = set(inspect.signature(pipeline.run_pipeline).parameters)
    assert params & FORBIDDEN_PARAMS == set()


def _param_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)
    return names


def test_no_pdft_engine_function_takes_an_api_key_parameter():
    bad = []
    for path in _engine_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if "api_key" in _param_names(node):
                bad.append(f"{path}:{node.lineno} {node.name}")
    assert bad == []


def test_shown_red_the_ast_scan_catches_a_planted_api_key_parameter(tmp_path):
    """Red-first proof that the scan above actually discriminates -- it is not a
    vacuous pass over files that could never fail it."""
    planted = tmp_path / "planted.py"
    planted.write_text("def f(x, *, api_key): ...\n", encoding="utf-8")
    tree = ast.parse(planted.read_text(encoding="utf-8"))
    bad = [
        f"{planted}:{node.lineno} {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "api_key" in _param_names(node)
    ]
    assert bad != []


def test_engine_lock_carries_no_provider_sdk():
    packages = {p["name"] for p in tomllib.loads(LOCK.read_text(encoding="utf-8"))["package"]}
    hit = packages & FORBIDDEN_PACKAGES
    assert hit == set(), hit
