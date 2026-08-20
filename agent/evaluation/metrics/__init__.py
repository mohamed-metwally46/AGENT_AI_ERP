"""Metrics: pure functions over recorded observations.

Pure on purpose. Every function here takes a `CaseResult` and returns a number
or a violation, with no database, no model and no clock -- which is what makes
"metrics are reproducible" testable rather than aspirational.
"""
