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

import {
  seedIsRealistic,
  BASE_URL,
  P95_GENERATION_S,
  SEED,
  TARGET,
  UPLOAD_ORIGIN,
} from './config.js';
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
    // A fourth, learned on 2026-09-20: the three above say the run was SET
    // UP as a baseline; this one says there was a platform to measure. That
    // run met all three, every one of its 526,629 requests failed in ~2ms,
    // and the file said `valid: true` because nothing had asked. §7 judges
    // an error RATE against its budget; this is coarser and earlier -- when
    // more of what the generator sent was refused than answered, the run
    // measured an outage, and an outage's percentiles describe the error
    // path, not the platform. (429s are not failures here, exactly as in
    // §7 item 4: a limiter shedding load is the platform answering.)
    platform_answered: failedRate(data) < 0.5,
    // A fifth, learned on 2026-09-26: `profile.js` has always said a non-zero
    // `dropped_iterations` invalidates the rate the report claims, and
    // nothing here read it. That run dropped 264,140 arrivals -- about half
    // of what the profile offered -- because every VU was parked on a 7s
    // response, and the file said `valid: true` for a 300 rps peak the
    // platform was never actually sent. A constant-arrival-rate profile that
    // did not arrive at its rate is a closed-loop test wearing its name.
    rate_delivered: droppedIterations(data) === 0,
  };
  validity.valid =
    validity.real_tokens &&
    validity.tls_edge &&
    validity.realistic_seed &&
    validity.platform_answered &&
    validity.rate_delivered;

  return {
    profile,
    // A run that fails any of the five conditions is not a baseline. Writing
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
      // Where the generator DIALLED presigned uploads, which is not
      // necessarily the address they were signed against (`lib/config.js`).
      // Empty means it used them exactly as issued.
      upload_origin: UPLOAD_ORIGIN,
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

// `aizzak_failed_requests` is recorded once per request AND once per socket
// attempt (`metrics.js`, `ws_hold.js`), so it is the one rate that covers
// everything the generator did. No samples at all -- k6 declares the metric
// at init, so it is present even when `setup()` threw and no VU ever ran --
// is the same answer as "all of them failed": k6 still writes the summary
// for an aborted run, and a rate of 0 over nothing must not read as a
// platform that answered everything.
function failedRate(data) {
  const v = ((data.metrics || {}).aizzak_failed_requests || {}).values || {};
  const samples = (v.passes || 0) + (v.fails || 0);
  return samples > 0 && typeof v.rate === 'number' ? v.rate : 1;
}

// Absent means k6 never had to drop one -- the metric is only emitted on the
// first drop -- so 0 is the honest reading of "not there".
function droppedIterations(data) {
  const v = ((data.metrics || {}).dropped_iterations || {}).values || {};
  return v.count || 0;
}

function counterTable(data) {
  const out = {};
  for (const [name, metric] of Object.entries(data.metrics || {})) {
    if (metric.type === 'counter') out[name] = metric.values.count;
    // A rate is not a count. The 2026-09-20 file carried
    // `http_req_failed: 1`, which reads as ONE failed request and meant ALL
    // of them (a rate of 1.0). `count` is the samples where the metric was
    // true -- for `http_req_failed`, the failures -- out of `total`.
    else if (metric.type === 'rate') {
      const v = metric.values || {};
      out[name] = {
        rate: v.rate,
        count: v.passes,
        total: (v.passes || 0) + (v.fails || 0),
      };
    }
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
