"""ele-parse: WireTwin's PDF inspection service (CMP-07, DEC-04, LLD 3.3.10).

A SEPARATE deployable (``services/ele_parse/Dockerfile``), never imported by ``app`` and importing
nothing from it: the only dependency is PyMuPDF (``INC1-D-8``), the server is the standard
library's. It has no database, no object-store credential and no secret. It reads the stage
volume read-only and answers JSON. Every request runs in a fresh child process under
``resource`` limits, so a hostile PDF can cost one bounded child and nothing else.
"""

SERVICE_VERSION = "1.0.0"
