// Custom metrics and the one shared response rule.
//
// k6's built-in `http_req_duration` is tagged per scenario and carries the
// three budgets that map onto whole requests. What it cannot express is the
// four quantities `docs/capacity-plan.md` actually gates on -- time to FIRST
// SSE token (not to the last), retrieval latency INSIDE a RAG request,
// end-to-end index time across a worker, and how long a WebSocket survived --
// so those are Trends here.

import { Counter, Rate, Trend } from 'k6/metrics';
import { check } from 'k6';

// 07 §2's "أوّل رمزٍ في البثّ" -- the budget is on the first token, so a
// stream that opens fast and finishes slowly PASSES, which is the intent.
export const ttft = new Trend('aizzak_ttft_ms', true);
// The RAG budget is the RETRIEVAL budget: embed + Qdrant search, `ح‑5`'s
// blast radius. Measured as the whole POST /knowledge/search, which is the
// closest a client-side harness can get and is stated as such in the README.
export const ragRetrieval = new Trend('aizzak_rag_retrieval_ms', true);
// Register -> PUT -> complete -> index -> `indexed`, across the worker. `ح‑6`
// is exactly this number failing to fall when replicas are added.
export const indexEndToEnd = new Trend('aizzak_index_e2e_ms', true);
export const wsHoldSeconds = new Trend('aizzak_ws_hold_seconds');
export const wsFrames = new Counter('aizzak_ws_frames');

// §7 item 4: "معدّل خطأ < 0.1% على المسارات المتزامنة (429 المقصود ليس خطأً)".
// So a 429 is counted, loudly, on its OWN metric and is NOT a failure -- once
// Wave 1 lands `ح‑7`'s limiter, a peak profile that provokes none of these is
// evidence the limiter is not wired, and one that answers only these is
// evidence it is mis-tuned. Neither reading survives folding them into the
// error rate.
export const rejected = new Counter('aizzak_rate_limited_total');
export const failures = new Rate('aizzak_failed_requests');

// Anything the platform is allowed to answer under load without it counting
// against the error budget. 429 by §7; 503 deliberately NOT included -- a
// shed request is a failure to serve, and the plan never licenses one.
const ACCEPTABLE = new Set([429]);

// One place that decides what "the request worked" means, so five scenarios
// cannot each answer it differently.
export function graded(res, name, expected) {
  const want = expected || [200];
  if (ACCEPTABLE.has(res.status)) {
    rejected.add(1, { route: name });
    failures.add(false);
    // A 429 without `Retry-After` is a bug in the limiter, not a rejection
    // the client can act on (`1.2`'s own acceptance criterion), so it is
    // checked here where every 429 in the whole harness passes through.
    check(res, { [`${name}: 429 carries Retry-After`]: (r) => !!r.headers['Retry-After'] });
    return false;
  }
  const ok = want.includes(res.status);
  failures.add(!ok);
  check(res, { [`${name}: ${want.join('/')}`]: () => ok });
  if (!ok && __ENV.LOAD_VERBOSE === '1') {
    console.error(`${name} -> ${res.status} ${String(res.body).slice(0, 300)}`);
  }
  return ok;
}
