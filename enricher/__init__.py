"""
Enricher package — Context enrichment for static technical debt findings (Phase 2).
"""

from enricher.callgraph_enricher import build_call_counts
from enricher.coverage_enricher import build_coverage_map
from enricher.enricher import enrich, print_enrichment_summary
from enricher.git_enricher import build_file_context

__all__ = [
    "enrich",
    "print_enrichment_summary",
    "build_file_context",
    "build_call_counts",
    "build_coverage_map",
]
