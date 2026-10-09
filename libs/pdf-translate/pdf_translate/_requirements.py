# -*- coding: utf-8 -*-
"""What this package needs from outside, checked before it is needed.

Installing the skill as a plugin copies files and runs nothing, so a machine
with no Python and no packages installs cleanly and fails at the first
command instead. That failure used to be
``ModuleNotFoundError: No module named 'pikepdf'``: one name, out of a
traceback, with no mention of the other two and no way to act on it.

This module names every missing package at once, gives a command bound to the
interpreter that is actually running, and says which interpreter that is -
because the usual cause is not a missing install, it is an install that went
to a different Python.

It imports nothing it checks for. It has to be importable on the machine that
has none of them, which is the only machine where it matters.
"""
import importlib.util
import sys

# (import name, pip name). They differ for fontTools, which is the one people
# get wrong: `pip install fontTools` works by accident of case-insensitivity,
# but the name on PyPI is fonttools.
REQUIREMENTS = (
    ('pymupdf', 'pymupdf'),
    ('pikepdf', 'pikepdf'),
    ('fontTools', 'fonttools'),
)

# What SKILL.md and pyproject.toml declare. Stated, never enforced here: the
# packages are what actually stop a run, and refusing to start on a version
# that works would be this module causing the outage it exists to explain.
DECLARED_PYTHON = '3.14+'


def missing_requirements():
    """The pip names of every required package that cannot be imported.

    Order follows REQUIREMENTS so the message is stable between runs.
    `find_spec` rather than a real import: this answers "is it installed"
    without paying for, or crashing inside, a heavy third-party import.
    """
    missing = []
    for module, pip_name in REQUIREMENTS:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            # A broken or half-removed install raises instead of returning
            # None. From here that is indistinguishable from absent, and
            # "reinstall it" is the right advice either way.
            found = False
        if not found:
            missing.append(pip_name)
    return tuple(missing)


def requirements_message(missing):
    """One paragraph: what is missing, how to fix it, and where it will land."""
    names = ', '.join(missing)
    were = 'is' if len(missing) == 1 else 'are'
    return (
        f'pdf-translate cannot start: {names} {were} not installed.\n'
        f'\n'
        f'Install into the interpreter that is running this:\n'
        f'    "{sys.executable}" -m pip install {" ".join(missing)}\n'
        f'\n'
        f'Running Python {sys.version.split()[0]} at {sys.executable}\n'
        f'This skill declares Python {DECLARED_PYTHON}. If you believe these '
        f'are already installed,\nthey are probably in a different interpreter '
        f'than the one above.'
    )


def guard_cli():
    """Print the message and exit 2 when anything is missing; else say nothing.

    Silence on the happy path is the contract: the eleven CLIs are compared
    byte for byte between checkouts, so a preflight that printed on success
    would be a console change on every job.
    """
    missing = missing_requirements()
    if not missing:
        return
    print(requirements_message(missing), file=sys.stderr)
    raise SystemExit(2)
