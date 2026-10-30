#!/usr/bin/env bash
# SBOM, licence policy and known vulnerabilities of the RUNTIME dependency
# closure -- PLT-19 (NFR-LIC-02, NFR-LIC-07, NFR-SEC-04).
#
#   deploy/supply-chain/scan.sh
#
# The one entry point: CI (job `supply-chain`) and a developer run the same
# command. Everything is written under build/supply-chain/ (git-ignored):
#
#   runtime-freeze.txt  pip freeze of the runtime environment
#   sbom.cdx.json       CycloneDX 1.6 SBOM of that environment
#   audit.json          pip-audit result
#   report.md           the checker's report
#
# Five steps:
#   1. a venv built the way the Dockerfile builds /opt/venv: `pip install
#      '.[parsers]'`, then `pip uninstall aizzak-platform`. `[dev]` never
#      enters the image, so it is not scanned. `pip freeze --all` keeps `pip`
#      in the frozen set, so it is audited like every other package: it ships
#      in the image's /opt/venv and is on its PATH (INC0-Q-10). The pip here is
#      the one THIS interpreter seeds, which need not be the image's (the image
#      seeds /opt/venv from the base image's Python); PLT-17 scans the image;
#   2. a second venv holding only the two pinned tools of requirements.txt;
#   3. the SBOM;
#   4. pip-audit over the frozen set. Its exit code is kept, not acted on: the
#      checker decides, and it must run even when audit found something;
#   5. deploy/supply-chain/supply_chain_check.py applies license-policy.toml.
#      Its exit code is this script's: 0 pass, 1 violation, 2 input missing.
#
# Needs the network (PyPI and the vulnerability database). Touches no service
# of the Compose stack. PYTHON chooses the interpreter that builds the venvs
# (python3.12 or newer, as the Dockerfile uses 3.12) and runs the checker.
set -euo pipefail

cd "$(dirname "$0")/../.."

python_bin="${PYTHON:-python3}"
out=build/supply-chain
mkdir -p "$out"

echo "== runtime environment (pip install '.[parsers]', as the Dockerfile does)"
rm -rf -- "$out/runtime"
"$python_bin" -m venv "$out/runtime"
"$out/runtime/bin/pip" install --quiet --disable-pip-version-check '.[parsers]'
"$out/runtime/bin/pip" uninstall --quiet --yes aizzak-platform
"$out/runtime/bin/pip" freeze --all >"$out/runtime-freeze.txt"
echo "$(wc -l <"$out/runtime-freeze.txt") packages frozen"

echo "== scan tools (deploy/supply-chain/requirements.txt)"
"$python_bin" -m venv "$out/tools"
"$out/tools/bin/pip" install --quiet --disable-pip-version-check \
  -r deploy/supply-chain/requirements.txt

# --gather-license-texts keeps the free-text `License:` field (protobuf, pymupdf,
# pypdfium2) that cyclonedx-py otherwise drops when it is not an SPDX id.
echo "== SBOM (CycloneDX 1.6)"
"$out/tools/bin/cyclonedx-py" environment "$out/runtime/bin/python" \
  --pyproject pyproject.toml --mc-type application --sv 1.6 --of JSON \
  --gather-license-texts --output-reproducible -o "$out/sbom.cdx.json"

echo "== known vulnerabilities (pip-audit)"
rm -f -- "$out/audit.json"
audit_rc=0
"$out/tools/bin/pip-audit" -r "$out/runtime-freeze.txt" --no-deps --disable-pip --strict \
  --progress-spinner off -f json -o "$out/audit.json" || audit_rc=$?
echo "pip-audit exit code: $audit_rc"

echo "== licence policy and vulnerability policy"
rc=0
"$python_bin" deploy/supply-chain/supply_chain_check.py \
  --sbom "$out/sbom.cdx.json" --audit "$out/audit.json" \
  --freeze "$out/runtime-freeze.txt" \
  --policy deploy/supply-chain/license-policy.toml \
  --report "$out/report.md" || rc=$?
cat "$out/report.md" 2>/dev/null || true
exit "$rc"
