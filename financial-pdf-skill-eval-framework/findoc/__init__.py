"""Stable, offline-safe entry point for the FinDoc document workflow.

The package intentionally starts as a thin T1 layer over the legacy standard
output contract.  It does not invoke LAS, OpenClaw, or the legacy evaluator.
"""

from findoc.workflow import AnalysisResult, FindocWorkflowError, analyze_document

__all__ = ["AnalysisResult", "FindocWorkflowError", "analyze_document"]
