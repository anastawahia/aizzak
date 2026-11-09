# WireTwin evaluation framework (FR-EVAL-01)

Tools for measuring the electrical module, not tests. No file here is named `test_*`,
so pytest collects nothing from this directory; the pure functions are unit-tested in
`tests/unit/test_electrical_eval_framework.py`.

| file | what it is |
|---|---|
| `framework.py` | the report shape (`MetricResult`, `EvalReport`), `write_report` (JSON, sorted keys), `compare` (deltas between two runs), `fingerprint` (sha256 over the inputs' own hashes) |
| `m04_identifiers.py` | metric M-04 for INC-1: the exact-match rate of the identifier parser on `cases/identifiers.toml`, with a breakdown by kind, convention, script and source |
| `cases/identifiers.toml` | the core case set: every format of SRS Appendix A with the exact shape the parser must return, plus other spellings of the same forms and the malformed forms that must be flagged |

## M-04 in INC-1

`tests/unit/test_electrical_shared_parser.py` reads the same cases, so a case that no
longer matches fails CI; the value in the report is 1.0 by construction (target R1:
at least 0.995). The vector-text part of M-04 arrives with INC-2.

```
.venv/bin/python -m tests.eval.electrical.m04_identifiers --out /tmp/m04.json
```

## Adding a case

A case lists `text` (and `hint` when the caller knows the kind), then the expected
`kind` (`"none"` = no identifier at all), `valid`, `convention`, `aspects`,
`connection_point`, `device_class`, `pole_role`, `variant`, `issues`, `plc`, and the
`canonical` form and `search_keys` (and, for F2, `kks`, `ansi` and `alternatives`). An absent
optional field means none. `select` (optional) names the `RuleSelection` fields the case is
parsed under (`conventions`, `cross_reference_format`, `extra_ansi_suffixes`); a case without
it is parsed with the packaged rules as they are. When the rules
change, bump `version` in `shared/rules/identifiers.toml` and `SHARED_VERSION`.
