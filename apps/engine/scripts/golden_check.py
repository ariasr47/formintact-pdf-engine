"""engine-boundary S0: rasterize a DELIVERED PDF and compare each page with its committed golden,
so the black-box harness (which never imports `api`) proves AC 10 on delivered bytes.
Usage: python golden_check.py <delivered.pdf> <golden_dir> <n_pages>; exit 0 iff every page passes."""
import sys
import tempfile
from pathlib import Path

from pdft_engine import pdfops
from pdft_engine.golden import passes


def main(argv: list[str]) -> int:
    pdf, golden_dir, n = Path(argv[1]), Path(argv[2]), int(argv[3])
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for page in range(1, n + 1):
            actual = Path(tmp) / f"actual-{page}.png"
            pdfops.render_for_golden_diff(pdf, page, actual)
            if not passes(golden_dir / f"page-{page}.png", actual):
                failures.append(page)
    print(f"golden_check pages={n} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
