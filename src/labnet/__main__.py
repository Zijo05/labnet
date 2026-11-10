"""Allow running the tool as ``python -m labnet``."""

import sys

from labnet.cli import main

if __name__ == "__main__":
    sys.exit(main())
