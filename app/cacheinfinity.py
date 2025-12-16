#!/usr/bin/env python3
"""CacheInfinity entrypoint script."""

import sys
from pathlib import Path

# Add the app directory to Python path so we can import modules
app_dir = Path(__file__).parent
sys.path.insert(0, str(app_dir))

from core.server import main


if __name__ == "__main__":
    main()