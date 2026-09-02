// Scenario 1 of §0.1: "تسجيل دخولٍ ثمّ تصفّح".
//
// ONE HTTP request per iteration, deliberately. k6's arrival-rate executors
// count ITERATIONS, so an iteration that fires a five-request page load turns
// a `rate: 253` profile into 1,265 rps against a target of 300 -- the report
// would say 300 and the platform would feel 1,265. The page-load burst §0
// cites as the justification for its ×6 peak factor is therefore modelled by
// the peak factor itself, not by batching here.
//
// The mix rotates on the iteration counter rather than randomly so two runs
// of the same profile issue the same request mix, which is what makes a
// baseline comparable to the thing it is a baseline for.

import http from 'k6/http';
import { API } from '../lib/config.js';
import { authHeaders, tokenForVu } from '../lib/auth.js';
import { graded } from '../lib/metrics.js';

const AGENT_KEY = __ENV.LOAD_AGENT_KEY || 'rag_agent';

export function browse() {
  const tok = tokenForVu();
  const slot = __ITER % 10;

  if (slot < 4) return read(tok, 'conversations', `${API}/conversations?limit=20`);
  if (slot < 6) return read(tok, 'spaces', `${API}/spaces?limit=20`);
  if (slot === 6) {
    return read(tok, 'files', `${API}/files?space_id=${tok.spaceId}&limit=20`);
  }
  if (slot === 7) return read(tok, 'me_context', `${API}/me/context`);
  if (slot === 8) {
    // A cheap, real write: the session heartbeat 03 declares. Chosen over a
    // creating endpoint for this slot precisely because it writes without
    // growing the corpus on every iteration.
    return write(tok, 'heartbeat', `${API}/me/heartbeat`, null, [200, 204]);
  }
  // The one creating write in the mix. It DOES grow the corpus -- roughly
  // 25/s at the peak profile, ~45k rows over a 30-minute run -- and that is
  // intended: a write path that never allocates a row measures a different
  // query plan than the one production runs. `python -m app.ops.purge` is the
  // cleanup, and the seed size recorded with the run is the before-figure.
  return write(tok, 'create_conversation', `${API}/conversations`, {
    space_id: tok.spaceId,
    agent_key: AGENT_KEY,
    title: `load ${__VU}-${__ITER}`,
  });
}

function read(tok, name, url) {
  const res = http.get(url, { headers: authHeaders(tok), tags: { op: 'read', route: name } });
  return graded(res, name);
}

function write(tok, name, url, body, expected) {
  const res = http.post(url, body === null ? null : JSON.stringify(body), {
    headers: authHeaders(tok),
    tags: { op: 'write', route: name },
  });
  return graded(res, name, expected || [200, 201]);
}
