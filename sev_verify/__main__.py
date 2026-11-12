"""python3 -m sev_verify  (or: python3 sev_verify)"""

import sys

if __package__:
    from .cli import main
else:
    # Run as ``python3 sev_verify``: Python executes this file as a top-level
    # script with the package directory as sys.path[0], so relative imports
    # fail. Put the repo root there instead and import the package by name.
    from pathlib import Path

    sys.path[0] = str(Path(__file__).resolve().parent.parent)
    from sev_verify.cli import main

sys.exit(main())
