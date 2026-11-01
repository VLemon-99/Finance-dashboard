"""Version codes for static files (CSS, JavaScript), so browsers never use an outdated copy.

Pages link to e.g. `/static/style.css?v=3f2a9c1e04b7`. The code is taken from the file's
content, so it changes exactly when the file changes. A browser that saved the old file
sees a new address and downloads the new one.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


def asset_version(name: str, static_dir: Path = STATIC_DIR) -> str:
    """Short code that changes whenever the file's content changes."""
    path = static_dir / name
    info = path.stat()
    # Re-hash only when the file's size or modification time changes.
    return _content_hash(path, info.st_mtime_ns, info.st_size)


@lru_cache(maxsize=64)
def _content_hash(path: Path, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
