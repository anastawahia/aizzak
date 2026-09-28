// Scenario 2 of §0.1: "استعلام RAG" -- 40/s at peak, and the only scenario on
// the synchronous path whose budget (400ms, 07 §2) covers work done by
// ANOTHER service: `embedding` (`ح‑5`) and Qdrant (`ح‑11`).
//
// This is the scenario that is expected to fail first on today's stack, and
// the expectation is the point: `ح‑5` says the embedding service is one
// process whose `model.encode` blocks its own event loop, so 40 concurrent
// queries serialize. Step 0.5's baseline is not complete until this number is
// written down.

import exec from 'k6/execution';
import http from 'k6/http';
import { API } from '../lib/config.js';
import { authHeaders, tokenForVu } from '../lib/auth.js';
import { graded, ragRetrieval } from '../lib/metrics.js';
import { ragQuery } from '../lib/queries.js';

// The question comes from `lib/queries.js`, not a fixed list. Six rotating
// questions were said to be "neither a cache-hit nor a cache-miss benchmark";
// once step 4.3's cache was on they were ~100% hits, and none of them shared
// a word with the seeded corpus (د‑37). That module says what the stream is
// instead, and why its repeat rate is an assumption the result must carry.
export function rag() {
  const tok = tokenForVu();
  // `k`, not `top_k`, and `space_id` is REQUIRED -- س-32 made a search span
  // one space or not run at all, and the audit measurement taken through the
  // cross-space version had to be withdrawn. A harness that reproduced that
  // request would reproduce that withdrawal.
  const body = {
    query: ragQuery(exec.scenario.name, exec.scenario.iterationInTest),
    space_id: tok.spaceId,
    k: 8,
  };

  const res = http.post(`${API}/knowledge/search`, JSON.stringify(body), {
    headers: authHeaders(tok),
    tags: { op: 'rag', route: 'knowledge_search' },
  });
  // Recorded for EVERY answered request including a 429, because a limiter
  // that sheds RAG load makes the surviving requests look fast and the trend
  // has to be readable next to `aizzak_rate_limited_total` to see that.
  ragRetrieval.add(res.timings.duration);
  return graded(res, 'knowledge_search');
}
