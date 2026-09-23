#!/usr/bin/env python3
"""
run_analysis.py — CLI entry point for the Technical Debt Analyzer.

Usage:
    python run_analysis.py --target /path/to/python/repo
    python run_analysis.py                             # uses default from config.py
"""

import argparse
import sys

from analyzer.analyzer import run_all, save_results, print_summary
from config import OUTPUT_FILE, TARGET_REPO_PATH


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run static-analysis tools and produce a unified tech-debt dataset.",
    )
    parser.add_argument(
        "--target",
        default=TARGET_REPO_PATH,
        help="Path to the Python repository to analyse (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help="Path to the output JSON file (default: %(default)s)",
    )
    args = parser.parse_args()

    # Run analysis
    issues = run_all(args.target)

    # Save
    out_path = save_results(issues, args.output)
    print(f"  Results saved to: {out_path}")

    # Summary
    print_summary(issues)

    return 0


if __name__ == "__main__":
    sys.exit(main())
