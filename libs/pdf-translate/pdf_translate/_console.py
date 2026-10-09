#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The one handler that reproduces this skill's console exactly.

Every stage logs; nothing prints. A CLI entry point wraps its work in
`console()` and the lines arrive on stdout byte for byte as `print` wrote them.
A consumer importing the library gets silence and a result object, and attaches
whatever handler its own service uses.

**Re-entrant, and that is not a nicety.** `pipeline.py` runs the other
stages in-process, not as subprocesses: loud functions such as `retypeset` and
`field_fonts`, and CLI entry points such as `extract_segments.main` and
`verify.main`. Each of those opens `console()` itself, so `pipeline.main()`'s
handler and the stage's would both be attached to the same logger and every
line would print twice. Nested uses share the outermost handler instead.

**CLI entry points only.** This mutates process-global logging state and is not
thread-safe. A service calls the silent `run_*` twins and attaches its own
handler; it never calls this. Silent calls avoid stdout interference but do
not make PyMuPDF thread-safe. Concurrent PDF jobs need separate processes and
job directories; see `references/consumer-guide.md`.
"""
import contextlib
import logging
import sys

PACKAGE_LOGGER = 'pdf_translate'

_depth = 0
_handler = None


def configure_cli_streams():
    """Match the script wrappers' UTF-8 output only when running a CLI."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError, ValueError):
            pass


def missing_option_value(argv, options):
    """Name an option whose value is absent, without interpreting positionals."""
    return next((value for index, value in enumerate(argv) if value in options and
                 (index + 1 == len(argv) or argv[index + 1].startswith('--'))), None)


def _arg(argv, name, default=None):
    """The token after `name`, or `default` when the option is absent.

    Every CLI's `_main` wants this and each used to spell it out. A dangling
    option — `name` as the last token — still raises, exactly as the inline
    form did; `missing_option_value` above is what a `_main` calls first to
    turn that into a usage line.
    """
    return argv[argv.index(name) + 1] if name in argv else default


@contextlib.contextmanager
def console(stream=None):
    """Attach one stdout handler to the package logger, re-entrantly.

    `stream` is for tests that need to prove where the bytes went; the CLIs
    always take the default, which is resolved at enter time so a caller that
    has already redirected `sys.stdout` is honoured.
    """
    global _depth, _handler
    pkg = logging.getLogger(PACKAGE_LOGGER)
    if _depth == 0:
        _handler = logging.StreamHandler(stream if stream is not None
                                         else sys.stdout)
        # The bare message: print(x) wrote `x\n`, and any prefix — a level, a
        # logger name, a timestamp — would be a console change. The console is
        # the contract.
        _handler.setFormatter(logging.Formatter('%(message)s'))
        pkg.addHandler(_handler)
        pkg.setLevel(logging.INFO)
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth == 0:
            pkg.removeHandler(_handler)
            _handler = None


def say(log, line):
    """Emit one console line, surviving a console that cannot encode it.

    `qa_check._say` has caught UnicodeEncodeError around `print` since before
    this module existed — a Japanese finding on a cp1252 terminal must not
    take the run down. The same guard belongs here, once, rather than at every
    call site.
    """
    try:
        log.info(line)
    except UnicodeEncodeError:
        log.info(line.encode('ascii', 'backslashreplace').decode('ascii'))
