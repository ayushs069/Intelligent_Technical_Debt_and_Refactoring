#!/usr/bin/env python3
"""
run_analysis.py — CLI entry point for the Technical Debt Analyzer.

Usage:
    python run_analysis.py --target /path/to/python/repo
    python run_analysis.py                             # uses default from config.py
"""

import argparse
import os
import sys

from analyzer.analyzer import run_all, save_results, print_summary
from config import TARGET_REPO_PATH


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run static-analysis tools and produce a unified tech-debt dataset.",
    )
    parser.add_argument(
        "--target",
        default=TARGET_REPO_PATH,
        help="Path or GitHub URL of the Python repository to analyse (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Custom path to the output JSON file (default: data/<repo_name>_debt_dataset.json)",
    )
    args = parser.parse_args()

    # Run analysis
    issues, repo_name = run_all(args.target)

    # Determine output paths
    if args.output:
        repo_output_path = args.output
    else:
        repo_output_path = os.path.join("data", f"{repo_name}_debt_dataset.json")

    master_output_path = os.path.join("data", "debt_dataset.json")

    # Save to per-repo output file
    out_path = save_results(issues, repo_output_path)
    print(f"  Repository dataset saved to: {out_path}")

    # Also update primary debt_dataset.json
    save_results(issues, master_output_path)
    print(f"  Primary dataset updated at:  {os.path.abspath(master_output_path)}")

    # Summary
    print_summary(issues)

    return 0


if __name__ == "__main__":
    sys.exit(main())
