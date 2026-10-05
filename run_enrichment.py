#!/usr/bin/env python3
"""
run_enrichment.py — CLI entry point for Phase 2: Repo Context Enrichment.

Usage:
    # Enrich a dataset produced from a local repo:
    python run_enrichment.py --repo /path/to/repo

    # Specify a custom Phase 1 input file:
    python run_enrichment.py --repo /path/to/repo --input data/requests_debt_dataset.json

    # Fully custom input/output:
    python run_enrichment.py --repo /path/to/repo \\
        --input data/requests_debt_dataset.json \\
        --output data/requests_enriched_dataset.json
"""

import argparse
import os
import sys

from enricher.enricher import enrich, print_enrichment_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2 — Enrich a tech-debt dataset with git, call-graph, and coverage context.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Path to the local Python repository (must be a git repo for git enrichment).",
    )
    parser.add_argument(
        "--input",
        default="",
        help=(
            "Path to the Phase 1 JSON dataset. "
            "Default: data/<repo_name>_debt_dataset.json"
        ),
    )
    parser.add_argument(
        "--output",
        default="",
        help=(
            "Path for the enriched output JSON. "
            "Default: data/<repo_name>_enriched_dataset.json"
        ),
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=90,
        help="Number of days of git history to consider (default: 90).",
    )
    args = parser.parse_args()

    repo_path = os.path.normpath(os.path.abspath(args.repo))
    repo_name = os.path.basename(repo_path)

    # Resolve default input/output paths
    input_path = args.input or os.path.join("data", f"{repo_name}_debt_dataset.json")
    output_path = args.output or os.path.join("data", f"{repo_name}_enriched_dataset.json")

    if not os.path.isfile(input_path):
        print(f"[error] Input dataset not found: {input_path}")
        print("        Run Phase 1 first:  python run_analysis.py --target <repo>")
        return 1

    enriched_path = enrich(
        phase1_path=input_path,
        repo_path=repo_path,
        output_path=output_path,
        lookback_days=args.lookback,
    )

    print_enrichment_summary(enriched_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
