// Every number this harness runs on, in ONE place, derived rather than typed.
//
// `docs/capacity-plan.md` §0 states the target and §3 states the equations
// that turn it into per-scenario arrival rates. If those two are re-stated by
// hand in five scenario files they will disagree within a week, and a load
// profile that no longer matches the target it claims to test is worse than
// no profile at all -- it produces a number people trust. So the targets are
// constants here and the rates are COMPUTED from them below; changing §0
// changes every scenario at once, and the arithmetic is visible.

// ── §0's binding targets ──────────────────────────────────────────────────
export const TARGET = {
  concurrentUsers: 500,
  apiRpsAverage: 50,
  apiRpsPeak: 300,
  wsConnections: 1500,
  llmStreamsConcurrent: 50,
  ragQpsPeak: 40,
  indexJobsPerMinute: 100,
  workspaces: 200,
};

// ── §3's provider-concurrency equation, run backwards ─────────────────────
// llm_concurrency = session starts/s × p95 generation duration
// We need 50 concurrent streams and can only control the arrival rate, so:
//   starts/s = 50 / p95_generation_seconds
// The duration is an ASSUMPTION until step 0.5 measures it, and it is
// declared as one: `LOAD_P95_GENERATION_S` overrides it, and the run summary
// records the value used. A number nobody can see is a number nobody can
// correct.
export const P95_GENERATION_S = Number(__ENV.LOAD_P95_GENERATION_S || 10);
export const STREAM_STARTS_PER_S = TARGET.llmStreamsConcurrent / P95_GENERATION_S;

// Indexing is stated per minute; k6 arrival rates are per second.
export const INDEX_STARTS_PER_S = TARGET.indexJobsPerMinute / 60;

// Browsing is the REMAINDER, not an independent knob: §0's 300 rps is the
// total across the edge, so giving each scenario its own absolute rate would
// silently test 395 rps while reporting 300.
export const BROWSE_RPS_PEAK = round2(
  TARGET.apiRpsPeak - TARGET.ragQpsPeak - STREAM_STARTS_PER_S - INDEX_STARTS_PER_S,
);
// The average profile scales the same split down by the peak factor §0
// derives (300 / 50 = 6), so the two profiles differ in intensity and in
// nothing else.
export const PEAK_FACTOR = TARGET.apiRpsPeak / TARGET.apiRpsAverage;

// ── 07-nfr-slo §2's time budgets, unrelaxed (§0's own statement) ──────────
// These are k6 threshold expressions, so a profile that misses a budget exits
// non-zero. A load test whose failure is a line of prose in a summary is a
// report; this is a gate.
export const BUDGET_MS = {
  read: 150,
  write: 250,
  ttft: 1200,
  ragRetrieval: 400,
};

// ── The edge under test ───────────────────────────────────────────────────
export const BASE_URL = (__ENV.LOAD_BASE_URL || 'https://localhost').replace(/\/+$/, '');
export const API = `${BASE_URL}/api/v1`;
export const WS_URL = `${BASE_URL.replace(/^http/, 'ws')}/api/v1/ws`;

// ── The three conditions §0.1 says void the whole result ──────────────────
// Written as VALUES rather than as prose in the README, because prose is not
// checked and this is: `validity()` below folds them into the run summary, and
// `assertRunnable()` refuses to start when the second one is broken.
//
// (١) real Firebase tokens and the whole auth path -- a stub authenticator
//     reproduces `07 §2`'s existing number and means nothing. Asserted by the
//     token file carrying `"stub": false` (see `auth.js`).
// (٢) through TLS and the real edge, never `app:8000` -- this one is REFUSED
//     rather than recorded, because a run against the app port measures a
//     different system, not a lenient version of this one.
// (٣) a realistically sized seed -- the operator states what was seeded and
//     the number is archived with the result. An unstated seed makes two runs
//     incomparable, which is the entire purpose of a baseline.
export const SEED = {
  declared: __ENV.LOAD_SEED_ID || '',
  messages: Number(__ENV.LOAD_SEED_MESSAGES || 0),
  files: Number(__ENV.LOAD_SEED_FILES || 0),
  vectors: Number(__ENV.LOAD_SEED_VECTORS || 0),
  workspaces: Number(__ENV.LOAD_SEED_WORKSPACES || 0),
};

// `0.5`'s baseline seed, from `docs/capacity-plan.md` §0.1's own text.
const SEED_FLOOR = { messages: 1_000_000, files: 100_000, vectors: 1_000_000, workspaces: 200 };

export function seedIsRealistic() {
  return (
    SEED.messages >= SEED_FLOOR.messages &&
    SEED.files >= SEED_FLOOR.files &&
    SEED.vectors >= SEED_FLOOR.vectors &&
    SEED.workspaces >= SEED_FLOOR.workspaces
  );
}

export function assertRunnable() {
  if (BASE_URL.startsWith('https://')) return;
  // An explicit acknowledgement exists because a smoke run against a
  // plaintext edge IS useful while the harness itself is being developed --
  // but it can never be a baseline, so it also forces `valid: false` into the
  // summary and cannot be mistaken for one later.
  if (__ENV.LOAD_ALLOW_PLAINTEXT === '1') return;
  throw new Error(
    `LOAD_BASE_URL is ${BASE_URL}. Condition (2) of capacity-plan.md 0.1 requires the run to ` +
      'cross TLS and the real nginx edge, not app:8000. Set LOAD_BASE_URL=https://localhost, ' +
      'or LOAD_ALLOW_PLAINTEXT=1 to run a harness smoke test that is marked invalid.',
  );
}

// The self-signed certificate `nginx-certs` generates (capacity-plan.md ح‑19).
// Skipping verification asserts nothing about the certificate and is not meant
// to: this run measures the cost of TLS TERMINATION, which is real whether or
// not the chain is trusted. The same `-k` the nginx healthcheck already uses,
// for the same reason.
//
// It is spread into a profile's exported `options`, never into a request's
// params: `insecureSkipTLSVerify` is a k6 OPTION, and k6's `http.Params` has
// no field by that name. Passing it per-request is silently ignored -- every
// request then fails certificate verification against a self-signed edge, and
// the run reads as a platform outage.
export const TLS_GLOBAL_OPTIONS = { insecureSkipTLSVerify: true };

function round2(n) {
  return Math.round(n * 100) / 100;
}
