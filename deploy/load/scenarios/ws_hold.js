// Scenario 5 of §0.1: "تعلّقُ WS" -- 1,500 idle-but-open sockets.
//
// This scenario measures what nothing else here does: the COST OF EXISTING.
// §0 puts 1,500 connections against `ح‑9`'s `worker_connections 1024` at the
// edge and `ح‑10`'s single Redis, which holds the `WsConnectionRegistry` for
// every one of them. Neither cost shows up in a throughput scenario, because
// neither is paid per request.
//
// The executor is `constant-vus`, not an arrival rate: the quantity under
// test is a POPULATION, and one VU holds one socket for the whole run.

import { WebSocket } from 'k6/net/websockets';
import { clearInterval, setInterval, setTimeout } from 'k6/timers';
import { WS_URL } from '../lib/config.js';
import { tokenForVu } from '../lib/auth.js';
import { failures, wsFrames, wsHoldSeconds } from '../lib/metrics.js';

// How long one VU keeps its socket before recycling. Deliberately shorter
// than the run so the profile also exercises RECONNECTION -- a population
// that is only ever established measures the steady state and misses the
// registry churn a rolling deploy or a flaky client network produces.
const HOLD_MS = Number(__ENV.LOAD_WS_HOLD_MS || 120000);
// 03 §3.2's `ping` verb. Idle does not mean silent: a proxy that closes idle
// sockets would otherwise look like the platform dropping connections.
const PING_MS = Number(__ENV.LOAD_WS_PING_MS || 30000);

export function wsHold() {
  const tok = tokenForVu();
  const socket = new WebSocket(`${WS_URL}?token=${encodeURIComponent(tok.idToken)}`, null, {
    tags: { op: 'ws', route: 'ws_hold' },
  });
  const openedAt = Date.now();
  let pinger = null;
  let opened = false;

  socket.onopen = () => {
    opened = true;
    failures.add(false);
    pinger = setInterval(() => socket.send(JSON.stringify({ type: 'ping' })), PING_MS);
    setTimeout(() => socket.close(), HOLD_MS);
  };

  socket.onmessage = () => {
    wsFrames.add(1);
  };

  socket.onclose = () => {
    if (pinger !== null) clearInterval(pinger);
    // Only a socket that actually opened contributes a hold time; a refused
    // upgrade contributes a failure instead. Recording 0 for a refusal would
    // pull the trend DOWN as the edge got worse.
    if (opened) wsHoldSeconds.add((Date.now() - openedAt) / 1000);
  };

  socket.onerror = () => {
    if (!opened) failures.add(true);
    if (pinger !== null) clearInterval(pinger);
  };
}
