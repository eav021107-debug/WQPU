#!/usr/bin/env python3
from __future__ import print_function

import argparse
import sys

from wqpu_operator_service import manage


def main():
    parser = argparse.ArgumentParser(prog="wqpu-testnet autostart")
    parser.add_argument("action", nargs="?", choices=("enable", "refresh", "disable", "status"), default="status")
    parser.add_argument("--script", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    return manage(args.action, args.script, args.state, args.config, python_exe=sys.executable)


if __name__ == "__main__":
    raise SystemExit(main())
