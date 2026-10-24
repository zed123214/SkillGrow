"""Adapters that turn existing, verified artifact contracts into DocumentIR."""

from findoc.adapters.legacy_standard import LegacyStandardAdapter, LegacyStandardAdapterError

__all__ = ["LegacyStandardAdapter", "LegacyStandardAdapterError"]
