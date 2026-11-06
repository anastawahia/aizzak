#!/usr/bin/env bash
# SBOM, licence policy and known vulnerabilities of the dependency closure of the two Python
# images the platform builds -- PLT-19 (NFR-LIC-02, NFR-LIC-07, NFR-SEC-04), plus the ele-parse
# environment of WireTwin INC-1 (INC1-Q-3 (a), O16).
#
#   deploy/supply-chain/scan.sh
#
# The one entry point: CI (job `supply-chain`) and a developer run the same
# command. Everything is written under build/supply-chain/ (git-ignored):
#
#   runtime-freeze.txt  pip freeze of the RUNTIME environment (the `app` image)
#   sbom.cdx.json       CycloneDX 1.6 SBOM of that environment
#   audit.json          pip-audit result of that environment
#   report.md           the report of BOTH environments (runtime first, then ele-parse), a
#                       note on the pymupdf versions, and the ele-parse freeze
#   ele-parse/          freeze.txt, sbom.cdx.json, audit.json, report.md of the second
#                       environment. ci.yml uploads only the four files above (INC1-Q-3 (a)
#                       leaves ci.yml unchanged), so the ele-parse evidence travels inside
#                       report.md: its component and licence table, its vulnerability table
#                       and its freeze. Its raw SBOM and audit.json stay in this directory.
#
# Steps:
#   1. RUNTIME venv built the way the Dockerfile builds /opt/venv: `pip install
#      pip==<pip_version>`, then `pip install '.[parsers]'`, then `pip uninstall
#      aizzak-platform`. `[dev]` never enters the image, so it is not scanned.
#      `pip freeze --all` keeps `pip` in the frozen set, so it is audited like every other
#      package (INC0-Q-10). The pip version below is the ONE pin of ./Dockerfile and
#      services/ele_parse/Dockerfile (tests/unit/test_supply_chain_check.py fails when the
#      three differ), so the scanned pip is the pip the images ship;
#   2. ELE-PARSE venv built from services/ele_parse/requirements.txt with `--require-hashes`,
#      the way services/ele_parse/Dockerfile installs it. PyMuPDF is AGPL-3.0 and is flagged
#      in the report for this environment too;
#   3. a venv holding only the two pinned tools of requirements.txt;
#   4. one SBOM and one pip-audit run per environment. The pip-audit exit code is kept, not
#      acted on: the checker decides, and it must run even when audit found something;
#   5. deploy/supply-chain/supply_chain_check.py applies license-policy.toml to EACH
#      environment. Both always run and this script exits with the worse code of the two
#      (0 pass, 1 violation, 2 input missing): a failure in one never hides the other.
#
# The two environments are different images and may hold different versions of the same
# package (pymupdf: the runtime one resolves `pymupdf>=1.24,<2`, ele-parse pins one version).
# They are never merged. report.md states both versions and says so when they differ; that is
# a note, not a failure, because the runtime range is open by design.
#
# Scanning built images (OS packages, the release image of ele-parse, the embedding image) is
# NOT here: it waits for the "server readiness" step (INC1-Q-3 (a)).
#
# Needs the network (PyPI and the vulnerability database). Touches no service
# of the Compose stack. PYTHON chooses the interpreter that builds the venvs
# (python3.12 or newer, as the Dockerfiles use 3.12) and runs the checker.
set -euo pipefail

cd "$(dirname "$0")/../.."

# The ONE pip pin (see above). 26.2 is the lowest release that fixes all six advisories
# of INC0-Q-10 (the highest fix, PYSEC-2026-3721 / CVE-2026-13346, is in 26.2).
pip_version=26.2

python_bin="${PYTHON:-python3}"
out=build/supply-chain
ele="$out/ele-parse"
mkdir -p "$out"
rm -rf -- "$ele"
mkdir -p "$ele"

echo "== runtime environment (pip==$pip_version, then pip install '.[parsers]', as the Dockerfile does)"
rm -rf -- "$out/runtime"
"$python_bin" -m venv "$out/runtime"
"$out/runtime/bin/pip" install --quiet --disable-pip-version-check "pip==$pip_version"
"$out/runtime/bin/pip" install --quiet --disable-pip-version-check '.[parsers]'
"$out/runtime/bin/pip" uninstall --quiet --yes aizzak-platform
"$out/runtime/bin/pip" freeze --all >"$out/runtime-freeze.txt"
echo "$(wc -l <"$out/runtime-freeze.txt") packages frozen"

echo "== ele-parse environment (services/ele_parse/requirements.txt, --require-hashes)"
rm -rf -- "$out/ele-parse-venv"
"$python_bin" -m venv "$out/ele-parse-venv"
"$out/ele-parse-venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes \
  -r services/ele_parse/requirements.txt
"$out/ele-parse-venv/bin/pip" freeze --all >"$ele/freeze.txt"
echo "$(wc -l <"$ele/freeze.txt") packages frozen"

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
# ele-parse has no pyproject of its own; the checker needs a root component, so a stub names it.
# It lives under build/ (git-ignored) and the root licence is reported, never evaluated.
cat >"$ele/pyproject.toml" <<'STUB'
[project]
name = "aizzak-ele-parse"
version = "0"
license = { text = "Proprietary" }
STUB
"$out/tools/bin/cyclonedx-py" environment "$out/ele-parse-venv/bin/python" \
  --pyproject "$ele/pyproject.toml" --mc-type application --sv 1.6 --of JSON \
  --gather-license-texts --output-reproducible -o "$ele/sbom.cdx.json"

echo "== known vulnerabilities (pip-audit)"
rm -f -- "$out/audit.json"
audit_rc=0
"$out/tools/bin/pip-audit" -r "$out/runtime-freeze.txt" --no-deps --disable-pip --strict \
  --progress-spinner off -f json -o "$out/audit.json" || audit_rc=$?
echo "pip-audit exit code (runtime): $audit_rc"
audit_ele_rc=0
"$out/tools/bin/pip-audit" -r "$ele/freeze.txt" --no-deps --disable-pip --strict \
  --progress-spinner off -f json -o "$ele/audit.json" || audit_ele_rc=$?
echo "pip-audit exit code (ele-parse): $audit_ele_rc"

echo "== licence policy and vulnerability policy"
rc=0
"$python_bin" deploy/supply-chain/supply_chain_check.py \
  --label "runtime (app image)" \
  --sbom "$out/sbom.cdx.json" --audit "$out/audit.json" \
  --freeze "$out/runtime-freeze.txt" \
  --policy deploy/supply-chain/license-policy.toml \
  --report "$out/report-runtime.md" || rc=$?
rc_ele=0
"$python_bin" deploy/supply-chain/supply_chain_check.py \
  --label "ele-parse image" \
  --sbom "$ele/sbom.cdx.json" --audit "$ele/audit.json" \
  --freeze "$ele/freeze.txt" \
  --policy deploy/supply-chain/license-policy.toml \
  --report "$ele/report.md" || rc_ele=$?
# The worse of the two codes, never the first one alone.
if ((rc_ele > rc)); then rc=$rc_ele; fi

# One report.md for CI: both reports, the pymupdf note, the ele-parse freeze.
pymupdf_of() { sed -n 's/^[Pp]y[Mm]u[Pp][Dd][Ff]==//p' "$1" | head -n 1; }
v_runtime="$(pymupdf_of "$out/runtime-freeze.txt")"
v_ele="$(pymupdf_of "$ele/freeze.txt")"
{
  echo "# Supply chain scan: two environments"
  echo
  echo "- runtime (app image): \`$out/runtime-freeze.txt\`, pip $pip_version"
  echo "- ele-parse image: \`$ele/freeze.txt\`, pip $pip_version, installed with --require-hashes"
  echo
  echo "## pymupdf in the two environments"
  echo
  echo "- runtime: \`pymupdf==${v_runtime:-absent}\`"
  echo "- ele-parse: \`pymupdf==${v_ele:-absent}\`"
  if [[ "${v_runtime:-absent}" == "${v_ele:-absent}" ]]; then
    echo "- The versions are the same."
  else
    echo "- NOTE: the versions DIFFER. The runtime one resolves \`pymupdf>=1.24,<2\`; the ele-parse"
    echo "  one is the pin of services/ele_parse/requirements.txt. Each environment is judged"
    echo "  on its own version above; nothing is merged."
  fi
  echo
  cat "$out/report-runtime.md" 2>/dev/null || echo "(runtime report missing)"
  echo
  cat "$ele/report.md" 2>/dev/null || echo "(ele-parse report missing)"
  echo
  echo "## ele-parse freeze (pip freeze --all)"
  echo
  echo '```'
  cat "$ele/freeze.txt"
  echo '```'
} >"$out/report.md"
rm -f -- "$out/report-runtime.md"
cat "$out/report.md"
echo "scan.sh exit code: $rc (runtime and ele-parse, the worse of the two)"
exit "$rc"
