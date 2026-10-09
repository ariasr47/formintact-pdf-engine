"""CI runs every test file without anyone naming it, and its font cache follows the fetcher.

R-69. The suite step used to name its 22 modules by hand, so a new module ran
nowhere until someone remembered to add it: test_retypeset_capture went about
38 hours unrun on main. These tests ask, for every directory that holds a test
file, whether a new test file dropped beside the others would be discovered
by a CI step.

R-97. The font cache key was the literal `noto-fonts-v3`. A cache hit is never
re-saved, so once the fetcher gained nine faces both legs downloaded them,
42,585 KB each, on every run. The key must change when the fetcher does, and
the cache must be saved before the suite runs: the tests write instanced
faces into tests/fonts (han_forms caches `-wght400` copies beside their
source), and a cached instance would be reused instead of being made by the
code under test.

The workflow is read as text: PyYAML is not a dependency, and the lines these
tests need are plain `run:`, `uses:` and `key:` lines.
"""
import fnmatch
import re
import shlex
import subprocess
import unittest
from pathlib import Path, PurePosixPath

# dev/repo/ -> the repository root, the way the other scripts under dev/ find it.
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github' / 'workflows' / 'tests.yml'
FETCHER = 'pdf-translate/tools/fetch_test_fonts.py'


def workflow():
    return WORKFLOW.read_text(encoding='utf-8')


def tracked_test_dirs():
    """Directories, relative to the root, of every tracked test file."""
    done = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True)
    if done.returncode != 0:
        raise unittest.SkipTest('git ls-files is unavailable here')
    paths = (PurePosixPath(p) for p in done.stdout.decode('utf-8').split('\0') if p)
    return sorted({p.parent for p in paths if p.suffix == '.py' and p.name.startswith('test_')})


def discover_steps(text):
    """(start directory relative to the root, file pattern) for each discover run: line."""
    working = re.search(r'^\s*working-directory:\s*(\S+)\s*$', text, re.M)
    base = PurePosixPath(working.group(1) if working else '.')
    steps = []
    for line in re.findall(r'^\s*run:\s*(.*unittest discover.*)$', text, re.M):
        args = shlex.split(line)
        options = dict(zip(args, args[1:]))
        start = PurePosixPath(options.get('-s', '.'))
        parts = []
        for part in (base / start).parts:  # resolve '..' without touching the disk
            if part == '..':
                parts.pop()
            elif part != '.':
                parts.append(part)
        steps.append((PurePosixPath(*parts), options.get('-p', 'test*.py')))
    return steps


def discovered(path, steps):
    """Whether unittest discovery from any step would load `path`.

    Discovery loads matching files in the start directory, and recurses only
    into subdirectories that are packages.
    """
    for start, pattern in steps:
        if not fnmatch.fnmatchcase(path.name, pattern):
            continue
        if path.parent == start:
            return True
        if start in path.parents:
            between = path.parent.relative_to(start).parts
            if all((ROOT / start / PurePosixPath(*between[:i + 1]) / '__init__.py').is_file()
                   for i in range(len(between))):
                return True
    return False


class DiscoveryTests(unittest.TestCase):
    def test_a_new_test_file_beside_any_existing_one_would_run(self):
        steps = discover_steps(workflow())
        missed = [str(d) for d in tracked_test_dirs()
                  if not discovered(d / 'test_zz_new_module.py', steps)]
        self.assertEqual(missed, [], 'a new test file in these directories would not run in CI')


class FontCacheTests(unittest.TestCase):
    def test_the_cache_key_changes_when_the_fetcher_does(self):
        keys = re.findall(r'^\s*key:\s*(.+?)\s*$', workflow(), re.M)
        self.assertTrue(any(f"hashFiles('{FETCHER}')" in key for key in keys), keys)

    def test_the_cache_is_saved_before_the_suite_writes_instances_there(self):
        text = workflow()
        self.assertNotIn('uses: actions/cache@', text,
                         'the combined action saves at the end of the job, after the tests ran')
        save, suite = text.find('uses: actions/cache/save@'), text.find('unittest discover -s tests')
        self.assertNotEqual(save, -1, 'no cache save step')
        self.assertNotEqual(suite, -1, 'no suite step')
        self.assertLess(save, suite)


if __name__ == '__main__':
    unittest.main()
