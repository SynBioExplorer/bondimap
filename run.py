#!/usr/bin/env python3
"""Entry point.  Usage:  python run.py [config.json]"""

import sys

from bondimap.build import run

if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    run(cfg_path)
