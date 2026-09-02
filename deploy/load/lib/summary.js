// The archived result -- §0.1's acceptance criterion, verbatim: "يُخرج
// p50/p95/p99 لكلّ سيناريو في ملفّ JSON مؤرشَف، ومعه commit SHA وبصماتُ الصور
// وحجمُ البذرة".
//
// Every one of those four is here, and the reason they are in the SAME file
// as the numbers is that a run's identity is not metadata about the result --
// it IS the result. A p95 of 180ms means nothing without the commit that
// produced it and the corpus it ran against; step 0.5 exists to compare later
// waves against this file, and a comparison between two runs whose seeds
// differed by an order of magnitude is not a comparison.

import { seedIsRealistic, BASE_URL, P95_GENERATION_S, SEED, TARGET } from './config.js';
import { TOKENS_ARE_REAL } from './auth.js';

// k6 hands `handleSummary` the whole end-of-test dataset; this reshapes the
// part a human or a later diff actually reads, and keeps the raw metrics
// alongside rather than instead.
export function buildSummary(profile, data) {
  const validity = {
    // §0.1's three conditions, evaluated rather than asserted in prose.
    real_tokens: TOKENS_ARE_REAL === true,
    tls_edge: BASE_URL.startsWith('https://'),
    realistic_seed: seedIsRealistic(),
  };
  validity.valid = validity.real_tokens && validity.tls_edge && validity.realistic_seed;

  return {
    profile,
    // A run that fails any of the three conditions is not a baseline. Writing
    // `false` into the file is what stops it becoming one by being the only
    // number anybody kept.
    valid: validity.valid,
    validity,
    finished_at: new Date().toISOString(),
    run: {
      // Filled by `run.sh`, which is the only thing that can see git and
      // docker. Empty strings when k6 was invoked by hand -- visibly empty,
      // never a plausible-looking default.
      commit: __ENV.RUN_COMMIT || '',
      dirty: __ENV.RUN_DIRTY === '1',
      images: safeJson(__ENV.RUN_IMAGES) || {},
      base_url: BASE_URL,
      k6_version: __ENV.RUN_K6_VERSION || '',
      host: __ENV.RUN_HOST || '',
    },
    seed: SEED,
    targets: TARGET,
    assumptions: {
      // Named because §3's provider equation runs on it and nothing has
      // measured it yet. When 0.5 does, this field is what says whether the
      // stream arrival rate in THIS run was right.
      p95_generation_s: P95_GENERATION_S,
    },
    thresholds: thresholdVerdicts(data),
    latency: latencyTable(data),
    counters: counterTable(data),
    raw: data.metrics,
  };
}

// Every threshold and whether it held -- the PASS/FAIL §7 item 11 demands,
// resolved per budget instead of as one opaque exit code.
function thresholdVerdicts(data) {
  const out = {};
  for (const [name, metric] of Object.entries(data.metrics || {})) {
    if (!metric.thresholds) continue;
    for (const [expr, verdict] of Object.entries(metric.thresholds)) {
      out[`${name} ${expr}`] = verdict.ok === true;
    }
  }
  return out;
}

function latencyTable(data) {
  const out = {};
  for (const [name, metric] of Object.entries(data.metrics || {})) {
    if (metric.type !== 'trend') continue;
    const v = metric.values || {};
    out[name] = {
      count: v.count,
      p50: pick(v, 'p(50)', 'med'),
      p95: v['p(95)'],
      p99: v['p(99)'],
      max: v.max,
      avg: v.avg,
    };
  }
  return out;
}

function counterTable(data) {
  const out = {};
  for (const [name, metric] of Object.entries(data.metrics || {})) {
    if (metric.type === 'counter') out[name] = metric.values.count;
    else if (metric.type === 'rate') out[name] = metric.values.rate;
  }
  return out;
}

function pick(v, ...keys) {
  for (const k of keys) if (v[k] !== undefined) return v[k];
  return undefined;
}

function safeJson(s) {
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}
