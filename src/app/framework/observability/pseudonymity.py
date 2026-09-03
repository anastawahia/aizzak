"""Pseudonymous identifiers for the aggregated log store (capacity 0.6).

Step 0.6 of ``docs/capacity-plan.md`` asks for one searchable store holding
every service's JSON lines, each carrying ``correlation_id``, ``event_id`` and
``workspace_id`` **مموّهاً** -- pseudonymised. This module is that last word,
and it exists because aggregation changes who reads a log line.

**What changes when logs are aggregated.** Before 0.6 a tenant identifier in a
log line was visible to whoever could already run ``docker logs`` on that host
-- an operator who could equally read the row itself out of Postgres. After
0.6 the same line lands in a store built to be queried by anyone with the
Grafana URL, kept for as long as retention says, and joined across services.
The identifier stops being an incidental by-product of a debugging session and
becomes a durable, indexed, cross-service tenant record. That is the change
this module answers.

**Why a plain digest, and not a keyed one.** The property 0.6's acceptance
criterion is built on is that ONE query gathers the edge line, the app line
and the worker line of a single failed request. That join only exists if every
process, in every container, across every restart, maps the same tenant to the
same string. A random per-process salt -- the reflex when hashing an
identifier -- would produce a different pseudonym in ``app`` than in
``worker-media`` and silently destroy exactly the property the step is for. A
deployment-wide salt would work, but it is one more secret to distribute to
five services to defend an identifier that is already a random UUIDv7: the
digest is not a password hash guarding a low-entropy secret, it is an opaque
handle standing in for a value nobody can enumerate.

**What this is NOT.** It is not anonymisation and does not claim to be. A
holder of a workspace id can compute its pseudonym and find that tenant's
lines -- that is a FEATURE (it is how an operator investigating a named
tenant's incident searches, `08-local-runbook §4.9`), and it is the reason the
word in the plan is «مموّه» and not «مجهول». It removes the identifier from
the store; it does not remove the link.

**Scope boundary, stated rather than assumed:** ``workspace_id`` is
pseudonymised, ``user_id`` is not. The tenant identifier appears on virtually
every line the platform emits, so leaving it raw builds a tenant-activity
index as a side effect of ordinary logging. ``user_id`` appears on a handful
of deliberate operator-facing lines (``auth.disabled_account``,
``auth.revoked_subject``) whose entire purpose is to let an operator confirm
which account was refused. Widening this to every identifier in sight would
make those lines useless without asking whether that trade was worth making.
"""

from __future__ import annotations

import hashlib

# 16 hex characters = 64 bits. Long enough that a collision inside one log
# store is not a thing that happens (the birthday bound is ~2^32 distinct
# tenants), short enough to read in a Grafana column and to type into a
# query by hand. The full 64 hex characters of SHA-256 would be neither.
_DIGEST_CHARS = 16

# A prefix, so a pseudonym is self-describing in a log line and in a query.
# Without it, `workspace_id: "3f9a1c2b8d4e5f60"` reads like an id from some
# other system and invites someone to go looking for the row it names.
_PREFIX = "ws-"


def pseudonymous_id(value: str) -> str:
    """Return the stable pseudonym for ``value`` -- ``ws-`` + 16 hex chars.

    Deterministic by design and across processes: the same workspace id yields
    the same pseudonym in the API container, in every worker, and after every
    restart. See this module's docstring for why that determinism is the
    requirement rather than a weakness.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{_PREFIX}{digest[:_DIGEST_CHARS]}"
