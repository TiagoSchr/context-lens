# context-lens: managed
#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL_SRC = ROOT / "src"
if LOCAL_SRC.exists():
    sys.path.insert(0, str(LOCAL_SRC))

from ctx.scripts.setup import main


if __name__ == "__main__":
    raise SystemExit(main())
