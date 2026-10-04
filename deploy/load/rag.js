// The RAG profile -- capacity 4.3's acceptance: §0's peak question rate
// (40/s), the RAG scenario alone, for the peak profile's 30 minutes.
//
//   deploy/load/run.sh rag
//
// LOAD_DURATION_S  seconds of arrivals (default 1800 -- `peak.js`'s 30 minutes)
// LOAD_RAG_QPS     questions a second (default 40, §0's peak). Lower it only
//                  for a host that cannot serve §0's rate: above the
//                  platform's ceiling the queue grows, k6 runs out of VUs and
//                  drops arrivals, and the replay below no longer describes
//                  what was asked. The share the stream offers rises with the
//                  rate (more repeats per window), so a lowered run is read
//                  against its own replay, never against 40/s's.
//
// 4.3 is judged by two numbers the five-part mix cannot give it
// (`lib/profile.js`, `buildRagOptions`): the query-vector cache's hit rate,
// and the retrieval p95 at 40 questions a second.
//
// The hit rate has two halves, and this file writes both beside each other.
// What the platform ANSWERED from its cache is its own count,
// `aizzak_embedding_cache_total`, which `run.sh` reads from every replica
// before and after the run. What the stream OFFERED it -- how many requests
// asked a question already embedded inside the cache window -- is the
// harness's assumption (`lib/queries.js`), so it is replayed here from the
// same function the scenario asked with, against the TTL the replicas
// actually run (`RUN_EMBEDDING_CACHE_TTL_S`, read by `run.sh`). The first
// says whether the cache works; only the two together say why the number is
// what it is.

import { buildRagOptions, guard } from './lib/profile.js';
import { buildSummary } from './lib/summary.js';
import { TARGET } from './lib/config.js';
import { ragRank } from './lib/queries.js';

const DURATION_S = Number(__ENV.LOAD_DURATION_S || 1800);
const QPS = Number(__ENV.LOAD_RAG_QPS || TARGET.ragQpsPeak);
const CACHE_TTL_S = Number(__ENV.RUN_EMBEDDING_CACHE_TTL_S || 0);

if (!(QPS > 0)) throw new Error(`LOAD_RAG_QPS must be a number > 0 (got ${__ENV.LOAD_RAG_QPS}).`);

export const options = buildRagOptions({ durationS: DURATION_S, qps: QPS });

export function setup() {
  return guard({ durationS: DURATION_S, wsVus: 0 });
}

export { rag } from './scenarios/rag.js';

export function handleSummary(data) {
  const out = __ENV.LOAD_OUT || 'deploy/load/results/rag-latest.json';
  const summary = buildSummary('rag', data);
  const count = (name) => (((data.metrics || {})[name] || {}).values || {}).count || 0;
  const offered = count('iterations');
  const repeats = CACHE_TTL_S > 0 ? offeredRepeats(offered, QPS, CACHE_TTL_S) : null;
  summary.rag = {
    qps: QPS,
    peak_qps: TARGET.ragQpsPeak,
    duration_s: DURATION_S,
    offered,
    dropped: count('dropped_iterations'),
    // A 429 never reaches retrieval, so it consults no cache: the replay
    // below assumes every arrival did, and this is what says whether it may.
    rate_limited: count('aizzak_rate_limited_total'),
    cache_ttl_s: CACHE_TTL_S || null,
    offered_repeats: repeats,
    offered_repeat_share: repeats === null || offered === 0 ? null : repeats / offered,
  };
  return {
    [out]: JSON.stringify(summary, null, 2),
    stdout: textSummary(summary),
  };
}

// The share the cache COULD answer: request i arrives at i / rate (a
// constant-arrival-rate scenario that dropped nothing), and its question was
// embedded within the TTL. The window opens at the MISS that wrote the entry
// and a hit does not reopen it -- `CachingEmbeddingProvider` writes only what
// it had to fetch -- so a question asked every few seconds still misses once
// per TTL. Two concurrent misses on one question are both counted here as
// one; at 40/s and ~100 ms that overlap is far below a request a minute.
function offeredRepeats(n, perSecond, ttlS) {
  const writtenAt = new Map();
  let repeats = 0;
  for (let i = 0; i < n; i++) {
    const t = i / perSecond;
    const r = ragRank('rag', i);
    const at = writtenAt.get(r);
    if (at !== undefined && t - at < ttlS) repeats++;
    else writtenAt.set(r, t);
  }
  return repeats;
}

function textSummary(summary) {
  const r = summary.rag;
  const p95 = ((summary.latency || {}).aizzak_rag_retrieval_ms || {}).p95;
  const rate = ((summary.counters || {}).aizzak_failed_requests || {}).rate;
  const share = r.offered_repeat_share;
  return (
    `\nrag: ${r.qps}/s for ${r.duration_s}s · offered ${r.offered} · dropped ${r.dropped} · ` +
    `rate-limited ${r.rate_limited} · retrieval p95 ${p95 === undefined ? '—' : Math.round(p95)}ms · ` +
    `errors ${rate === undefined ? '—' : (rate * 100).toFixed(3)}%\n` +
    `     the stream repeated ${share === null ? '—' : (share * 100).toFixed(1) + '%'} ` +
    `inside a ${r.cache_ttl_s || '?'}s window; what the cache answered is the platform's count (run.sh)\n`
  );
}
