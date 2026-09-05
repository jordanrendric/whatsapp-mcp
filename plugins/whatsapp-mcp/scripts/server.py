#!/usr/bin/env python3
"""Launch from any working directory without installing this source package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from whatsapp_mcp.server import main

if __name__ == "__main__":
    main()
