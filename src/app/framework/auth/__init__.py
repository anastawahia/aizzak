"""Authentication kernel pieces shared by the API guard and the operational
entrypoints (3.79). Nothing here talks to Firebase — that is the 2.7 adapter's
job; this package holds only what BOTH a request path and a revocation tool
must agree on."""
