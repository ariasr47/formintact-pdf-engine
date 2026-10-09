"""S1-engine-cut (PLAN.md, AC 14 at unit level): `run_pipeline`'s signature carries no
key, provider or translator -- the sink is the only seam between the engine and the
app's provider/key concerns, checked both at the live signature and by an AST scan of
every function definition in the engine set (so a NEW function cannot reintroduce one
under a different name)."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pdft_engine import pipeline

REPO = Path(__file__).resolve().parents[3]
FORBIDDEN_PARAMS = {"api_key", "translator", "provider", "model", "attempt_controller"}

# Same engine-side module set the import ratchet (test_import_boundary.py) tracks.
# engine-boundary S2-move: these files moved from apps/api/api(/jobs) to apps/engine/pdft_engine
# (git mv, flat -- no more jobs/ subdirectory), and engine_settings.py / engine_service.py were
# renamed to settings.py / service.py in the same move.
_ENGINE_DIR = REPO / "apps" / "engine" / "pdft_engine"
ENGINE_FILES = (
    _ENGINE_DIR / "pdfops.py",
    _ENGINE_DIR / "pipeline.py",
    _ENGINE_DIR / "skill_placement.py",
    _ENGINE_DIR / "masking.py",
    _ENGINE_DIR / "skill_widgets.py",
    _ENGINE_DIR / "skill_identifiers.py",
    _ENGINE_DIR / "golden.py",
    _ENGINE_DIR / "estimate_units.py",
    _ENGINE_DIR / "settings.py",
    _ENGINE_DIR / "service.py",
    _ENGINE_DIR / "prepared_state.py",
)
RETIRED_ENGINE_FILES = tuple(
    _ENGINE_DIR / name
    for name in ("fit.py", "layout.py", "obstacles.py", "type0.py")
)


def test_engine_files_all_exist():
    """The `path.is_file()` guard below exists for tmp_path-style shown-red tests
    elsewhere in this suite, not to let a stale ENGINE_FILES entry vanish silently."""
    missing = [str(p) for p in ENGINE_FILES if not p.is_file()]
    assert missing == []


def test_retired_engine_algorithms_are_absent():
    assert [str(path) for path in RETIRED_ENGINE_FILES if path.exists()] == []


def test_run_pipeline_signature_carries_no_key_provider_or_translator():
    params = set(inspect.signature(pipeline.run_pipeline).parameters)
    assert params & FORBIDDEN_PARAMS == set()


def test_no_engine_function_takes_an_api_key_parameter():
    bad = []
    for path in ENGINE_FILES:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {a.arg for a in (
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
            )}
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
            if "api_key" in names:
                bad.append(f"{path}:{node.lineno} {node.name}")
    assert bad == []
