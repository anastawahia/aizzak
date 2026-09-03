// Scenario 3 of §0.1: "بثّ وكيل" -- and the ONE scenario that produces the
// `ح‑1` number, time to first token.
//
// ── Why the WebSocket path and not `POST /agents/{key}/invoke` with SSE ────
// 07 §2's budget is on the FIRST token, not the last. k6's `http` module
// buffers a whole response before returning, so it can time a stream's END
// and its time-to-first-BYTE, neither of which is time-to-first-TOKEN: the
// response headers of an SSE stream flush when the response opens, which is
// before the model has produced anything. Timing the first byte and calling
// it TTFT would understate `ح‑1` -- the single most important number in this
// plan -- by however long the model actually took to start.
//
// `/api/v1/ws` gives one callback per FRAME, so `token` is observable exactly
// when it arrives. It is also the path the product uses (03 §3.2), which
// makes this the honest measurement rather than merely the convenient one.
// The SSE face of `/agents/{key}/invoke` stays untested by this harness and
// `README.md` §5 says so plainly rather than leaving it looking covered.

import http from 'k6/http';
// ⚠️ `k6/experimental/websockets`, NOT `k6/net/websockets` -- MEASURED, not
// assumed. Both files here imported the latter until the generator was first
// actually executed (capacity blocker د‑3): every k6 in the 1.x line, 1.3.0
// included, answers `GoError: unknown module: k6/net/websockets` and aborts
// the run at init. The graduated name does not exist yet; the experimental
// one is what ships. `k6/timers` IS graduated and is imported as such.
import { WebSocket } from 'k6/experimental/websockets';
import { clearTimeout, setTimeout } from 'k6/timers';
import { API, WS_URL } from '../lib/config.js';
import { authHeaders, tokenForVu } from '../lib/auth.js';
import { failures, graded, ttft } from '../lib/metrics.js';

const AGENT_KEY = __ENV.LOAD_AGENT_KEY || 'rag_agent';
const PROMPT = __ENV.LOAD_PROMPT || 'لخّص لي أهمّ ثلاث نقاط في المستندات المتاحة.';
// Generous next to a 1.2s budget, and it is not a budget: it bounds a VU that
// would otherwise hold a socket for the rest of the run when the provider
// queue (`ح‑1`) never gets to it. A run where this fires often has already
// answered the question it was asked.
const STREAM_TIMEOUT_MS = Number(__ENV.LOAD_STREAM_TIMEOUT_MS || 60000);

// One conversation per VU, created on first use and reused after. A new
// conversation per iteration would add a write to a scenario whose rate is
// derived from §3's provider equation, i.e. it would spend the API budget
// measuring something else -- and a user with one long thread is the more
// realistic shape anyway.
let conversationId = null;

export function stream() {
  const tok = tokenForVu();
  if (conversationId === null) {
    const res = http.post(
      `${API}/conversations`,
      JSON.stringify({ space_id: tok.spaceId, agent_key: AGENT_KEY, title: `load-stream ${__VU}` }),
      { headers: authHeaders(tok), tags: { op: 'write', route: 'create_conversation' } },
    );
    if (!graded(res, 'create_conversation', [200, 201])) return;
    conversationId = res.json('id');
  }

  const socket = new WebSocket(`${WS_URL}?token=${encodeURIComponent(tok.idToken)}`, null, {
    tags: { op: 'stream', route: 'ws_invoke' },
  });
  let sentAt = 0;
  let sawToken = false;

  const guard = setTimeout(() => {
    // Counted as a failure explicitly: without this, a stream that never
    // starts leaves NO sample in `aizzak_ttft_ms`, and a p95 computed over
    // only the streams that did start is a p95 of the successes -- the exact
    // shape of a metric that looks healthiest when the system is worst.
    failures.add(true);
    socket.close();
  }, STREAM_TIMEOUT_MS);

  socket.onopen = () => {
    sentAt = Date.now();
    socket.send(
      JSON.stringify({
        type: 'invoke',
        agent_key: AGENT_KEY,
        conversation_id: conversationId,
        // `AgentRequest.input` is a Json OBJECT, and `rag_agent` reads
        // `input["text"]` (agents/rag_agent/agent.py) -- a bare string here
        // is a 422 before the model is ever reached.
        input: { text: PROMPT },
      }),
    );
  };

  socket.onmessage = (e) => {
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    if (msg.type === 'token' && !sawToken) {
      sawToken = true;
      ttft.add(Date.now() - sentAt);
    }
    if (msg.type === 'final') {
      failures.add(false);
      clearTimeout(guard);
      socket.close();
    } else if (msg.type === 'error') {
      // A pre-flight refusal (unknown agent, quota, bad key) leaves the socket
      // open by contract; here it ends the iteration, and it counts -- a run
      // whose streams all 429 has not measured `ح‑1`.
      failures.add(true);
      clearTimeout(guard);
      socket.close();
    }
  };

  socket.onerror = () => {
    failures.add(true);
    clearTimeout(guard);
  };
}
