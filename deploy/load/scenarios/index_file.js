// Scenario 4 of §0.1: "رفعُ ملفٍّ وفهرسته" -- 100 jobs/minute, end to end.
//
// This is the only scenario that crosses the ASYNCHRONOUS boundary, and it is
// the one that produces `ح‑6`'s number. `ح‑6` was a serial loop -- one
// document per worker replica at a time -- and capacity 5.1 replaced it with
// up to `WORKER_CONCURRENCY` (4) per replica, so `worker-knowledge` at
// `replicas: 2` has up to eight in flight. §3's replica
// equation needs `p95 زمن المهمّة` as an input and nobody has measured it;
// `aizzak_index_e2e_ms` is that input.
//
// Four API calls and a poll loop per job, and the accounting is stated rather
// than hidden: at 1.67 jobs/s the four calls are ~6.7 rps of §0's 300, and the
// polls are tagged `op: poll` so they stay OUT of the read budget -- they are
// an artifact of measuring from outside, not traffic a user generates.

import http from 'k6/http';
import { sleep } from 'k6';
import { API, uploadTarget } from '../lib/config.js';
import { authHeaders, tokenForVu } from '../lib/auth.js';
import { failures, graded, indexEndToEnd } from '../lib/metrics.js';

// How often to ask whether the worker is done. A fixed 2s was measured to BE
// load: on 2026-09-26 the polls were 16,477 of the 121,868 requests the app
// served (13.5% -- more than `/files` and `/me/context` together), because
// 285 index VUs were asking every 2s once the workers fell behind: the harness
// adding ~140 rps exactly when the platform could least afford it. So the
// interval grows with the job's age -- a twentieth of it, which bounds the
// overshoot of an e2e sample at ~5% -- from POLL_INTERVAL_S to POLL_MAX_S. A
// healthy ~50s job is polled almost exactly as before; a 5-minute one about 60
// times instead of 150.
const POLL_INTERVAL_S = Number(__ENV.LOAD_INDEX_POLL_S || 2);
const POLL_MAX_S = Number(__ENV.LOAD_INDEX_POLL_MAX_S || 10);
// Bounds a VU whose document never reaches a terminal state -- a stalled
// worker, a DLQ'd envelope, a sealed Vault (`ح‑14`) starving the pipeline of
// MinIO credentials. Timing out is recorded as a failure, never as a fast
// success and never as a missing sample.
const INDEX_TIMEOUT_S = Number(__ENV.LOAD_INDEX_TIMEOUT_S || 300);

// Real-ish content: Arabic and Latin in one document, because the chunker and
// the multilingual embedding model both behave differently on each, and a
// corpus of lorem ipsum measures neither. Built on a VU's FIRST job, not at
// module load: every VU runs every module's init code whatever scenario it
// serves, so a document built up there sat in every VU of the other four
// scenarios -- 2,837 of the 3,122 alive when the 2026-09-26 run was killed --
// at ~70 KiB apiece (the string is UTF-16 inside the VM).
let body = null;

export function indexFile() {
  if (body === null) body = buildDocument();
  const tok = tokenForVu();
  const startedAt = Date.now();
  const name = `load-${__VU}-${__ITER}-${startedAt}.txt`;

  // 1) register
  const reg = http.post(
    `${API}/files`,
    JSON.stringify({
      space_id: tok.spaceId,
      name,
      content_type: 'text/plain',
      size_bytes: body.length,
    }),
    { headers: authHeaders(tok), tags: { op: 'write', route: 'register_file' } },
  );
  if (!graded(reg, 'register_file', [201])) return;
  const fileId = reg.json('file_id');
  const uploadUrl = reg.json('upload_url');

  // 2) PUT the bytes. This one request does NOT cross nginx: the presigned
  // URL is signed against `MINIO_PUBLIC_ENDPOINT` (SigV4 covers the host, so
  // it cannot be proxied), which is also how a browser uploads in production.
  // Condition (٢) of §0.1 is about the API path, and this is not it.
  //
  // What the generator CAN change is the address it dials, while sending the
  // host the URL was signed against -- `uploadTarget()` in `lib/config.js`
  // explains why that is necessary from inside a container and why it leaves
  // the platform untouched.
  const target = uploadTarget(uploadUrl);
  const put = http.put(target.url, body, {
    headers: { 'Content-Type': 'text/plain', ...target.headers },
    tags: { op: 'upload', route: 'minio_put' },
  });
  if (!graded(put, 'minio_put', [200])) return;

  // 3) complete -- `checksum: null` is the contract's own honest answer for a
  // client that did not hash its upload.
  const done = http.post(`${API}/files/${fileId}/complete`, JSON.stringify({ checksum: null }), {
    headers: authHeaders(tok),
    tags: { op: 'write', route: 'complete_file' },
  });
  if (!graded(done, 'complete_file', [200])) return;

  // 4) index -- the ONLY way anything is ever indexed. `Idempotency-Key`
  // because a retried POST buys a second document and the same embeddings
  // twice, which under load is a self-inflicted amplification.
  const idx = http.post(`${API}/knowledge/documents`, JSON.stringify({ file_id: fileId }), {
    headers: authHeaders(tok, { 'Idempotency-Key': `load-${fileId}` }),
    tags: { op: 'write', route: 'index_file' },
  });
  if (!graded(idx, 'index_file', [202])) return;
  const documentId = idx.json('id');

  // 5) wait for the worker
  const deadline = Date.now() + INDEX_TIMEOUT_S * 1000;
  for (;;) {
    if (Date.now() > deadline) {
      failures.add(true);
      return;
    }
    const ageS = (Date.now() - startedAt) / 1000;
    sleep(Math.min(POLL_MAX_S, Math.max(POLL_INTERVAL_S, ageS / 20)));
    const doc = http.get(`${API}/knowledge/documents/${documentId}`, {
      headers: authHeaders(tok),
      tags: { op: 'poll', route: 'get_document' },
    });
    if (doc.status !== 200) continue;
    const status = doc.json('status');
    if (status === 'indexed') {
      indexEndToEnd.add(Date.now() - startedAt);
      failures.add(false);
      return;
    }
    if (status === 'failed') {
      failures.add(true);
      return;
    }
  }
}

function buildDocument() {
  const ar =
    'تنصّ السياسةُ على أنّ الإجازةَ السنويّةَ ثلاثون يوماً، تُحتسب من تاريخ المباشرة، ' +
    'ولا تُرحَّل أكثرُ من خمسةَ عشرَ يوماً إلى السنة التالية. ';
  const en =
    'Retention: operational logs are kept for 90 days, audit records for seven years, ' +
    'and backups are verified by a quarterly restore drill. ';
  let out = '';
  // ~40 KB: past the chunker's first boundary in both scripts, small enough
  // that 100 uploads a minute is a corpus and not a disk-fill test.
  for (let i = 0; i < 120; i++) out += `${i}. ${ar}${en}\n`;
  return out;
}
