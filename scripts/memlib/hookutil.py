"""Small helpers shared by the two hooks.

The recall marker is a per-session file listing the memory ids already injected,
so the same memory is not re-injected later in the same session. Both hooks need
its path: recall.py writes it, session_start.py deletes it when the session is
cleared or compacted (the injected memories have left the context, so they must
become injectable again).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def recall_marker(session_id: str) -> Path:
    tmp = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "nosession"
    return tmp / f"persona-recall-{safe}.json"
