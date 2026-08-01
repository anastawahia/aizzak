"""Operational entrypoints — the steps 01-data-model §6 places OUTSIDE the
schema (seeding, grants, role wiring), run as their own short-lived processes.

Same footing as ``app.workers``: each module here is a composition root for
its own process (import-linter contract 6 names worker entrypoints as such,
and lists neither package among its source modules), so it may reach into
``app.infrastructure`` directly rather than through the API's root.
"""
