// Scenario 5 of §0.1: "تعلّقُ WS" -- 1,500 idle-but-open sockets.
//
// This scenario measures what nothing else here does: the COST OF EXISTING.
// §0 puts 1,500 connections against `ح‑9`'s `worker_connections 1024` at the
// edge and `ح‑10`'s single Redis, which holds the `WsConnectionRegistry` for
// every one of them. Neither cost shows up in a throughput scenario, because
// neither is paid per request.
//
// The executor is `constant-vus`, not an arrival rate: the quantity under
// test is a POPULATION. Each VU holds several sockets -- `profile.js` says how
// many through the scenario's env, `lib/config.js` says why -- and each socket
// SLOT behaves exactly as a one-socket VU used to: held for HOLD_MS, replaced
// at once when it drops, retried after a back-off when it is refused.

// ⚠️ `k6/experimental/websockets`, NOT `k6/net/websockets` -- MEASURED, not
// assumed. Both files here imported the latter until the generator was first
// actually executed (capacity blocker د‑3): every k6 in the 1.x line, 1.3.0
// included, answers `GoError: unknown module: k6/net/websockets` and aborts
// the run at init. The graduated name does not exist yet; the experimental
// one is what ships. `k6/timers` IS graduated and is imported as such.
import { WebSocket } from 'k6/experimental/websockets';
import { clearInterval, clearTimeout, setInterval, setTimeout } from 'k6/timers';
import { WS_URL } from '../lib/config.js';
import { tokenForSlot } from '../lib/auth.js';
import { failures, wsFrames, wsHoldSeconds } from '../lib/metrics.js';

// How long each socket is held before it is recycled. Deliberately shorter
// than the run so the profile also exercises RECONNECTION -- a population
// that is only ever established measures the steady state and misses the
// registry churn a rolling deploy or a flaky client network produces.
const HOLD_MS = Number(__ENV.LOAD_WS_HOLD_MS || 120000);
// 03 §3.2's `ping` verb. Idle does not mean silent: a proxy that closes idle
// sockets would otherwise look like the platform dropping connections.
const PING_MS = Number(__ENV.LOAD_WS_PING_MS || 30000);
// How long a slot waits after a REFUSED upgrade before it tries again. Without
// it, a refused socket is retried the instant it fails, and 1,500 refused
// sockets become a handshake storm: the 2026-09-20 peak run opened
// 165,609 sockets in thirty minutes -- 90 fresh TLS handshakes a second, every
// one of them rejected, none of them the population §0 describes. A real
// client that is refused backs off; so does this one.
const REJECT_BACKOFF_MS = Number(__ENV.LOAD_WS_REJECT_BACKOFF_MS || 5000);

export function wsHold() {
  // Read per iteration, not at init: a scenario's env reaches its VUs when
  // they run it, and VUs are initialised before they are handed a scenario.
  const slots = Number(__ENV.LOAD_WS_SOCKETS_THIS_VU || 1);
  const until = Date.now() + HOLD_MS;
  for (let slot = 0; slot < slots; slot++) hold(tokenForSlot(slot, slots), until);
}

// One socket, held until `until`. The iteration ends when every slot's event
// loop work has -- k6 ends an iteration when its loop is empty, so a pending
// timer IS the wait, for the hold and for the back-off alike.
function hold(tok, until) {
  const socket = new WebSocket(`${WS_URL}?token=${encodeURIComponent(tok.idToken)}`, null, {
    tags: { op: 'ws', route: 'ws_hold' },
  });
  const openedAt = Date.now();
  let pinger = null;
  let closer = null;
  let opened = false;
  let settled = false;

  // A refused upgrade fires `onerror` AND `onclose`; a dropped socket may
  // fire either. Whatever arrives first decides what happens next, once:
  // a socket that was open is replaced at once (a one-socket VU started its
  // next iteration immediately), a refused one after the back-off, and one
  // whose hold simply ran out is not replaced -- the next iteration does that.
  const settle = () => {
    if (settled) return;
    settled = true;
    if (pinger !== null) clearInterval(pinger);
    if (closer !== null) clearTimeout(closer);
    const delay = opened ? 0 : REJECT_BACKOFF_MS;
    if (Date.now() + delay < until) setTimeout(() => hold(tok, until), delay);
  };

  socket.onopen = () => {
    opened = true;
    failures.add(false);
    // `send` on a CLOSING socket throws (`InvalidStateError`, measured on
    // 1.3.0), and a throw ends the VU's whole iteration -- every other socket
    // it holds with it. A ping due at the instant of the hold's own close is
    // exactly that; the first 90-second smoke of this design hit it 3 times.
    pinger = setInterval(() => {
      if (socket.readyState === 1) socket.send(JSON.stringify({ type: 'ping' }));
    }, PING_MS);
    closer = setTimeout(() => {
      clearInterval(pinger);
      socket.close();
    }, Math.max(0, until - Date.now()));
  };

  socket.onmessage = () => {
    wsFrames.add(1);
  };

  socket.onclose = () => {
    // Only a socket that actually opened contributes a hold time; a refused
    // upgrade contributes a failure instead. Recording 0 for a refusal would
    // pull the trend DOWN as the edge got worse.
    if (opened) wsHoldSeconds.add((Date.now() - openedAt) / 1000);
    settle();
  };

  socket.onerror = () => {
    if (!opened) failures.add(true);
    settle();
  };
}
