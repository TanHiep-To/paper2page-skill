#!/usr/bin/env python3
"""paper2page: build a project page for one paper from an HTML template and publish it to GitHub."""
import sys

from p2plib.cli import main
from p2plib.common import P2PError

if __name__ == "__main__":
    try:
        sys.exit(main())
    except P2PError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
