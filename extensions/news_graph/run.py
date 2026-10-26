#!/usr/bin/env python3
"""Standalone launcher; never imports or installs the producer."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from newsmap_sidecar.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
