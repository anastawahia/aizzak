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
  BUDGET_MS,
  P95_GENERATION_S,
  SEED,
  TARGET,
  UPLOAD_ORIGIN,
} from './config.js';
import { TOKENS_ARE_REAL } from './auth.js';

// k6 hands `handleSummary` the whole end-of-test dataset; this reshapes the
// part a human or a later diff actually reads, and keeps the raw metrics
// alongside rather than instead.
export function buildSummary(profile, data, { steps: plan } = {}) {
  const steps = plan ? stepTable(plan, data) : null;
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
    //
    // The STEP profile is the one place drops are expected: a step above the
    // ceiling drops by construction, and that is its finding. There the
    // condition moves to the LOWEST step -- if even that one could not be
    // delivered, no step measured a rate and the run found nothing -- and
    // each step carries its own `delivered` in `steps`.
    rate_delivered: steps ? steps.length > 0 && steps[0].delivered : droppedIterations(data) === 0,
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
    ...(steps ? { steps, knee: kneeOf(steps) } : {}),
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

// One row per step of `step.js`, read from the `{step:<label>}` submetrics
// `lib/profile.js` materialises. `delivered` is the rate condition applied to
// the step alone; `within_budget` adds 07 §2's four latency budgets and §7's
// error budget, judged against this step's own requests.
function stepTable(plan, data) {
  const m = data.metrics || {};
  const val = (name) => (m[name] || {}).values || {};
  return plan.map((step) => {
    const t = `step:${step.label}`;
    const trend = (name) => {
      const v = val(name);
      return v.count ? { count: v.count, p50: pick(v, 'p(50)', 'med'), p95: v['p(95)'], p99: v['p(99)'] } : null;
    };
    const read = trend(`http_req_duration{op:read,${t}}`);
    const write = trend(`http_req_duration{op:write,${t}}`);
    const rag = trend(`aizzak_rag_retrieval_ms{${t}}`);
    const ttft = trend(`aizzak_ttft_ms{${t}}`);
    const failed = val(`aizzak_failed_requests{${t}}`);
    const samples = (failed.passes || 0) + (failed.fails || 0);
    const errorRate = samples > 0 ? failed.rate : 1;
    const dropped = val(`dropped_iterations{${t}}`).count || 0;
    const iterations = val(`iterations{${t}}`).count || 0;
    const delivered = iterations > 0 && dropped === 0;
    const under = (trendRow, budget) => !trendRow || trendRow.p95 < budget;
    return {
      label: step.label,
      rps: step.rps,
      start_s: step.startS,
      hold_s: step.holdS,
      served_http_rps: (val(`http_reqs{${t}}`).count || 0) / step.holdS,
      iterations,
      dropped_iterations: dropped,
      delivered,
      error_rate: errorRate,
      within_budget:
        delivered &&
        errorRate < 0.001 &&
        under(read, BUDGET_MS.read) &&
        under(write, BUDGET_MS.write) &&
        under(rag, BUDGET_MS.ragRetrieval) &&
        under(ttft, BUDGET_MS.ttft),
      p95: {
        read: read && read.p95,
        write: write && write.p95,
        rag: rag && rag.p95,
        ttft: ttft && ttft.p95,
      },
      latency: { read, write, rag, ttft, index_e2e: trend(`aizzak_index_e2e_ms{${t}}`) },
    };
  });
}

// The knee is the highest step BELOW which every step also held: a platform
// that fails at 100 and recovers at 150 has not shown it serves 150, it has
// shown noise, and the figure a later wave is compared against must not be
// the lucky one.
function kneeOf(steps) {
  const highest = (ok) => {
    let rps = null;
    for (const s of steps) {
      if (!ok(s)) break;
      rps = s.rps;
    }
    return rps;
  };
  return {
    sustained_rps: highest((s) => s.delivered && s.error_rate < 0.001),
    within_budget_rps: highest((s) => s.within_budget),
  };
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
