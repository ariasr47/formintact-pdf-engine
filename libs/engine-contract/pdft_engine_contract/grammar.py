"""The mask-token grammar, ONE definition for both processes (moved from masking.py:160-161,193)."""
import re

MARKER_OPEN = "«"
MARKER_CLOSE = "»"
MASK_TOKEN_RE = re.compile(f"{MARKER_OPEN}[a-z]{{2}}{MARKER_CLOSE}")
