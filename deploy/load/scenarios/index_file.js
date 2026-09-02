// Scenario 4 of §0.1: "رفعُ ملفٍّ وفهرسته" -- 100 jobs/minute, end to end.
//
// This is the only scenario that crosses the ASYNCHRONOUS boundary, and it is
// the one that produces `ح‑6`'s number: `engine.py:250` dispatches a batch
// with a plain `for` loop, so one worker replica indexes one document at a
// time and `worker-knowledge` at `replicas: 2` indexes two. §3's replica
// equation needs `p95 زمن المهمّة` as an input and nobody has measured it;
// `aizzak_index_e2e_ms` is that input.
//
// Four API calls and a poll loop per job, and the accounting is stated rather
// than hidden: at 1.67 jobs/s the four calls are ~6.7 rps of §0's 300, and the
// polls are tagged `op: poll` so they stay OUT of the read budget -- they are
// an artifact of measuring from outside, not traffic a user generates.

import http from 'k6/http';
import { sleep } from 'k6';
import { API } from '../lib/config.js';
import { authHeaders, tokenForVu } from '../lib/auth.js';
import { failures, graded, indexEndToEnd } from '../lib/metrics.js';

const POLL_INTERVAL_S = Number(__ENV.LOAD_INDEX_POLL_S || 2);
// Bounds a VU whose document never reaches a terminal state -- a stalled
// worker, a DLQ'd envelope, a sealed Vault (`ح‑14`) starving the pipeline of
// MinIO credentials. Timing out is recorded as a failure, never as a fast
// success and never as a missing sample.
const INDEX_TIMEOUT_S = Number(__ENV.LOAD_INDEX_TIMEOUT_S || 300);

// Real-ish content: Arabic and Latin in one document, because the chunker and
// the multilingual embedding model both behave differently on each, and a
// corpus of lorem ipsum measures neither.
const BODY = buildDocument();

export function indexFile() {
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
      size_bytes: BODY.length,
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
  const put = http.put(uploadUrl, BODY, {
    headers: { 'Content-Type': 'text/plain' },
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
    sleep(POLL_INTERVAL_S);
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
