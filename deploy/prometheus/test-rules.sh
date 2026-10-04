#!/usr/bin/env bash
# Check the alert rules and fire every one of them on synthetic series --
# capacity 7.3 (docs/capacity-plan.md), 08 §4.28.
#
#   deploy/prometheus/test-rules.sh
#
# Three checks, each with the binary of the image Compose actually runs, so a
# rule that parses here parses in production:
#   1. promtool check rules     -- alerts.yml is valid PromQL and YAML;
#   2. promtool test rules      -- alerts.test.yml: every rule fires on the
#                                  shape it exists for, and stays silent on
#                                  the healthy one;
#   3. amtool check-config      -- deploy/alertmanager/alertmanager.yml.
#
# ⚠️ Step 2 runs against a COPY of alerts.yml with the annotations removed.
# promtool compares annotations exactly, so keeping them would force every
# test case to restate four paragraphs of prose; the annotations are checked
# on their own by tests/unit/test_prometheus_alert_rules.py.
#
# Needs Docker, and a Python with PyYAML (`.venv/bin/python` locally, the
# CI interpreter in CI) -- set PYTHON to choose.
set -euo pipefail

cd "$(dirname "$0")/../.."

python_bin="${PYTHON:-}"
if [ -z "$python_bin" ]; then
  if [ -x .venv/bin/python ]; then python_bin=.venv/bin/python; else python_bin=python3; fi
fi

image_of() {
  # The tag docker-compose.yml pins, so this checks with what runs.
  grep -m1 -E "^\s+image: $1:" docker-compose.yml | awk '{print $2}'
}
prom_image="$(image_of prom/prometheus)"
am_image="$(image_of prom/alertmanager)"
[ -n "$prom_image" ] && [ -n "$am_image" ] || {
  echo "test-rules: could not read the prometheus/alertmanager image from docker-compose.yml" >&2
  exit 2
}

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
# The images run as `nobody`; mktemp's 0700 would hide the copy from them.
chmod 0755 "$work"

"$python_bin" - "$work/alerts.yml" <<'PY'
import sys
import yaml

with open("deploy/prometheus/alerts.yml", encoding="utf-8") as f:
    doc = yaml.safe_load(f)
for group in doc["groups"]:
    for rule in group["rules"]:
        rule.pop("annotations", None)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
PY
cp deploy/prometheus/alerts.test.yml "$work/alerts.test.yml"
chmod 0644 "$work"/*.yml

echo "== promtool check rules (${prom_image})"
docker run --rm -v "$PWD/deploy/prometheus:/rules:ro" --entrypoint promtool \
  "$prom_image" check rules /rules/alerts.yml

echo "== promtool test rules"
docker run --rm -v "$work:/t:ro" -w /t --entrypoint promtool \
  "$prom_image" test rules alerts.test.yml

echo "== amtool check-config (${am_image})"
docker run --rm -v "$PWD/deploy/alertmanager:/am:ro" --entrypoint amtool \
  "$am_image" check-config /am/alertmanager.yml

echo "test-rules: OK"
