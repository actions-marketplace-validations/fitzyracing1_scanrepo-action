#!/usr/bin/env python3
"""Restore a scanrepo booklet into a git repo and verify printed blob, tree and commit ids.

    python3 scanrepo-restore.py sample.scanrepo            # from the digital master
    python3 scanrepo-restore.py scans/ out/                # from page photos or payload .txt files
    python3 scanrepo-restore.py booklet.pdf out/           # from the PDF (needs pdftoppm + zbar)

The code lives in scanrepo/core.py and scanrepo/restore.py (standard library only).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scanrepo.restore import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
