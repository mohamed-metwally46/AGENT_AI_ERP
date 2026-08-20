"""The evaluation layer (Plan.md sections 11 and 12).

Deliberately outside ``src/erp_agent``: section 5.2 says the evaluation layer
must not run in the request path, and the cleanest way to guarantee that is for
the shipped package not to contain it. An architecture test asserts nothing
under ``src/`` imports this.
"""
