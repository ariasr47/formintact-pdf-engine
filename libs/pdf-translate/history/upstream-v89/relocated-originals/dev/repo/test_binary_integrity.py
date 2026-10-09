"""The committed binaries survive a checkout on every platform.

Git guesses at text vs binary by looking for a NUL byte. A small,
uncompressed PDF has none, so git calls it text, and a checkout with
`core.autocrlf=true` rewrites its LF bytes to CRLF. Every byte offset in
the xref table then points a few bytes short and the file is damaged —
silently, because it still opens after a repair pass.

That happened to `corpus/pale_blank.pdf`: 646 bytes in the repository,
693 bytes and `is_repaired=True` in a Windows working tree. The repository
`.gitattributes` pins it as binary. These tests fail if that rule is lost,
if a new binary type arrives without one, or if a damaged copy is ever
committed. They matter most on the Windows CI leg, where the smudge
actually happens.
"""
import subprocess
import unittest
from pathlib import Path

import pymupdf

# Git's own heuristic: a NUL inside the first 8000 bytes means binary.
SNIFF_BYTES = 8000

# This file lives at dev/repo/, so the root is two directories up — the same
# way every other script under dev/ resolves it. Asking git for
# `rev-parse --show-toplevel` would spawn a process per test method to learn
# what the path already says.
ROOT = Path(__file__).resolve().parents[2]


def git(cwd, *args):
    """Run git in `cwd` and return stdout, or None if it is unavailable or fails."""
    try:
        done = subprocess.run(
            ('git',) + args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def has_nul(path):
    """True if a NUL byte falls inside git's sniff window."""
    with path.open('rb') as handle:
        return b'\0' in handle.read(SNIFF_BYTES)


class BinaryIntegrityTests(unittest.TestCase):
    def setUp(self):
        # A directory in a clone, a file in a linked worktree; either means
        # the listings below have something to read.
        if not (ROOT / '.git').exists():
            self.skipTest('not a git work tree; nothing to check')
        # Pathspecs resolve against the working directory, so every listing
        # below runs at the root or it only sees this directory.
        self.root = ROOT

    def tracked(self, *patterns):
        listing = git(self.root, 'ls-files', '-z', '--', *patterns)
        if listing is None:
            self.skipTest('git ls-files failed')
        return [self.root / name for name in listing.split('\0') if name]

    def text_attribute(self, paths):
        """Map each path to its `text` attribute: set, unset or unspecified."""
        relative = [p.relative_to(self.root).as_posix() for p in paths]
        # NUL separators and raw bytes on purpose: a text-mode pipe rewrites
        # the separator to CRLF on Windows and git reads the CR as part of
        # the filename, which turns every answer into "unspecified".
        payload = b''.join(name.encode('utf-8') + b'\0' for name in relative)
        done = subprocess.run(
            ('git', 'check-attr', '-z', '--stdin', 'text'),
            cwd=self.root,
            input=payload,
            capture_output=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace'))
        fields = done.stdout.decode('utf-8').split('\0')
        # -z emits a flat <path> NUL <attribute> NUL <value> NUL stream.
        return {fields[i]: fields[i + 2] for i in range(0, len(fields) - 2, 3)}

    def test_gitattributes_pins_lf(self):
        attributes = self.root / '.gitattributes'
        self.assertTrue(attributes.is_file(), 'the repository has no .gitattributes')
        self.assertIn('eol=lf', attributes.read_text(encoding='utf-8'))

    def test_every_tracked_pdf_opens_unrepaired(self):
        pdfs = self.tracked('*.pdf')
        self.assertGreater(len(pdfs), 0, 'expected tracked PDFs to check')
        damaged = []
        for path in pdfs:
            if not path.is_file():
                continue
            with pymupdf.open(path) as doc:
                if doc.is_repaired:
                    damaged.append(path.relative_to(self.root).as_posix())
        self.assertEqual(
            damaged,
            [],
            'these PDFs needed a repair pass, which is what a CRLF smudge does '
            'to the xref offsets: ' + ', '.join(damaged),
        )

    def test_binary_files_are_declared_binary(self):
        paths = [p for p in self.tracked() if p.is_file()]
        sniffed = [p for p in paths if has_nul(p)]
        if not sniffed:
            self.skipTest('no tracked file carries a NUL byte')
        attributes = self.text_attribute(sniffed)
        undeclared = sorted(
            name
            for name, value in attributes.items()
            if value != 'unset'
        )
        self.assertEqual(
            undeclared,
            [],
            'these are binary but .gitattributes does not say so, so a Windows '
            'checkout may rewrite their line endings: ' + ', '.join(undeclared),
        )


if __name__ == '__main__':
    unittest.main()
