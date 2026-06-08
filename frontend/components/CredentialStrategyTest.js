'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createCapture, extractFeatures } from '../vendor/index.js';

const STORAGE_KEYS = {
  apiBase: 'cadence.chunk_test.api_base',
  apiKey: 'cadence.chunk_test.api_key',
  userId: 'cadence.chunk_test.user_id',
  threshold: 'cadence.chunk_test.threshold',
  chunkSize: 'cadence.chunk_test.chunk_size',
  runId: 'cadence.chunk_test.run_id',
  enrollmentAttempts: 'cadence.chunk_test.enrollment_attempts',
  sequenceHash: 'cadence.chunk_test.sequence_hash',
  sequenceLength: 'cadence.chunk_test.sequence_length'
};

const DEFAULT_ENROLLMENT_REQUIRED = 5;

function randomId(prefix) {
  const bytes = new Uint8Array(8);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return `${prefix}_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
}

function readStoredValue(key, fallback) {
  if (typeof window === 'undefined') return fallback;
  return window.localStorage.getItem(key) || fallback;
}

function defaultApiBase() {
  if (typeof window !== 'undefined') {
    return `${window.location.protocol}//${window.location.hostname}:5000`;
  }
  return process.env.NEXT_PUBLIC_SYNERGYZE_API_BASE || 'http://localhost:5000';
}

function clampInteger(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function sampleKeyCount(sample) {
  return (sample?.events || []).filter((event) => event.type === 'down').length;
}

function keyCodeSequence(sample) {
  return (sample?.events || [])
    .filter((event) => event.type === 'down')
    .map((event) => event.code);
}

async function hashKeyCodeSequence(sample) {
  const sequence = keyCodeSequence(sample);
  const payload = JSON.stringify(sequence);
  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(payload)
    );
    const hash = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, '0')
    ).join('');
    return { hash, length: sequence.length };
  }

  let hash = 0;
  for (let index = 0; index < payload.length; index += 1) {
    hash = ((hash << 5) - hash + payload.charCodeAt(index)) | 0;
  }
  return { hash: String(hash), length: sequence.length };
}

function formatScore(score) {
  if (typeof score !== 'number') return 'No score';
  return `${Math.round(score * 1000) / 10}%`;
}

function normalizeChunkKeystrokes(keystrokes) {
  return keystrokes.map((keystroke, index) => {
    if (index !== 0) return keystroke;
    return {
      ...keystroke,
      flight_time: null,
      down_down: null,
      up_up: null
    };
  });
}

function chunkSample(sample, chunkSizeValue) {
  const chunkSize = clampInteger(chunkSizeValue, 11, 4, 16);
  const features = extractFeatures(sample);
  const keystrokes = features.keystrokes || [];
  if (keystrokes.length === 0) return [];

  if (keystrokes.length <= chunkSize) {
    return [
      {
        index: 0,
        start: 0,
        end: keystrokes.length,
        raw_data: { keystrokes: normalizeChunkKeystrokes(keystrokes) }
      }
    ];
  }

  const stride = Math.max(1, Math.floor(chunkSize / 2));
  const lastStart = keystrokes.length - chunkSize;
  const starts = [];
  for (let start = 0; start <= lastStart; start += stride) {
    starts.push(start);
  }
  if (starts[starts.length - 1] !== lastStart) {
    starts.push(lastStart);
  }

  return starts.map((start, index) => {
    const end = start + chunkSize;
    return {
      index,
      start,
      end,
      raw_data: {
        keystrokes: normalizeChunkKeystrokes(keystrokes.slice(start, end))
      }
    };
  });
}

function aggregateScore(scores) {
  const numeric = scores.filter((score) => typeof score === 'number');
  if (numeric.length === 0) return null;
  return numeric.reduce((sum, score) => sum + score, 0) / numeric.length;
}

async function parseResponse(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export default function CredentialStrategyTest() {
  const passwordRef = useRef(null);
  const captureRef = useRef(null);
  const [apiBase, setApiBase] = useState(() =>
    readStoredValue(STORAGE_KEYS.apiBase, defaultApiBase())
  );
  const [apiKey, setApiKey] = useState(() =>
    readStoredValue(STORAGE_KEYS.apiKey, process.env.NEXT_PUBLIC_CADENCE_TEST_API_KEY || '')
  );
  const [userId, setUserId] = useState(() =>
    readStoredValue(STORAGE_KEYS.userId, 'chunk_test_user')
  );
  const [threshold, setThreshold] = useState(() =>
    readStoredValue(STORAGE_KEYS.threshold, '0.70')
  );
  const [chunkSize, setChunkSize] = useState(() =>
    readStoredValue(STORAGE_KEYS.chunkSize, '11')
  );
  const [runId, setRunId] = useState(() =>
    readStoredValue(STORAGE_KEYS.runId, randomId('run'))
  );
  const [enrollmentAttempts, setEnrollmentAttempts] = useState(() =>
    Number(readStoredValue(STORAGE_KEYS.enrollmentAttempts, '0')) || 0
  );
  const enrollmentAttemptsRef = useRef(enrollmentAttempts);
  const [sequenceBaseline, setSequenceBaseline] = useState(() => ({
    hash: readStoredValue(STORAGE_KEYS.sequenceHash, ''),
    length: Number(readStoredValue(STORAGE_KEYS.sequenceLength, '0')) || 0
  }));
  const sequenceBaselineRef = useRef(sequenceBaseline);
  const [status, setStatus] = useState({ message: 'Ready', kind: '' });
  const [busy, setBusy] = useState(false);
  const [enrollment, setEnrollment] = useState(null);
  const [lastSample, setLastSample] = useState(null);
  const [lastScore, setLastScore] = useState(null);
  const [chunkResults, setChunkResults] = useState([]);
  const [history, setHistory] = useState([]);

  const baseExternalUserId = useMemo(
    () => `chunk-test:${userId.trim()}:${runId}`,
    [userId, runId]
  );

  useEffect(() => {
    sequenceBaselineRef.current = sequenceBaseline;
    enrollmentAttemptsRef.current = enrollmentAttempts;
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(STORAGE_KEYS.apiBase, apiBase);
    window.localStorage.setItem(STORAGE_KEYS.apiKey, apiKey);
    window.localStorage.setItem(STORAGE_KEYS.userId, userId);
    window.localStorage.setItem(STORAGE_KEYS.threshold, threshold);
    window.localStorage.setItem(STORAGE_KEYS.chunkSize, chunkSize);
    window.localStorage.setItem(STORAGE_KEYS.runId, runId);
    window.localStorage.setItem(STORAGE_KEYS.enrollmentAttempts, String(enrollmentAttempts));
    if (sequenceBaseline.hash) {
      window.localStorage.setItem(STORAGE_KEYS.sequenceHash, sequenceBaseline.hash);
      window.localStorage.setItem(STORAGE_KEYS.sequenceLength, String(sequenceBaseline.length));
    } else {
      window.localStorage.removeItem(STORAGE_KEYS.sequenceHash);
      window.localStorage.removeItem(STORAGE_KEYS.sequenceLength);
    }
  }, [
    apiBase,
    apiKey,
    userId,
    threshold,
    chunkSize,
    runId,
    enrollmentAttempts,
    sequenceBaseline
  ]);

  const destroyCapture = useCallback(() => {
    if (!captureRef.current) return;
    try {
      captureRef.current.session.destroy();
    } catch {}
    captureRef.current = null;
  }, []);

  const startCapture = useCallback(() => {
    destroyCapture();
    const target = passwordRef.current;
    if (!target) return;

    let sample = null;
    let rejection = null;
    const session = createCapture({
      target,
      mode: 'password',
      minLength: 1,
      onSample: (readySample) => {
        sample = readySample;
      }
    });

    session.on('sample_rejected', (event) => {
      rejection = event.reason;
    });
    session.on('error', (event) => {
      setStatus({ message: event.error?.message || 'Capture failed', kind: 'error' });
    });
    session.start();

    captureRef.current = {
      session,
      finalize() {
        sample = null;
        rejection = null;
        session.stop();
        return { sample, rejection };
      }
    };
  }, [destroyCapture]);

  useEffect(() => {
    startCapture();
    return destroyCapture;
  }, [startCapture, destroyCapture]);

  const resetPasswordField = useCallback(() => {
    if (passwordRef.current) {
      passwordRef.current.value = '';
      window.setTimeout(() => {
        passwordRef.current?.focus();
      }, 0);
    }
    window.setTimeout(startCapture, 0);
  }, [startCapture]);

  const finalizeSample = useCallback(() => {
    const capture = captureRef.current;
    if (!capture) {
      return { error: 'Capture is not ready.' };
    }

    const { sample, rejection } = capture.finalize();
    if (!sample) {
      return { error: rejection ? `Sample rejected: ${rejection}` : 'No typing sample captured.' };
    }

    setLastSample(sample);
    return { sample };
  }, []);

  const callCadence = useCallback(async (path, body) => {
    const response = await fetch(`${apiBase.replace(/\/+$/, '')}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey.trim()}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });
    const json = await parseResponse(response);
    if (!response.ok) {
      const message = json?.message || json?.error || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return json;
  }, [apiBase, apiKey]);

  const chunkExternalUserId = useCallback((chunk) => (
    `${baseExternalUserId}:size-${clampInteger(chunkSize, 11, 4, 16)}:chunk-${chunk.index}`
  ), [baseExternalUserId, chunkSize]);

  const submitSample = useCallback(async () => {
    if (!apiBase.trim() || !apiKey.trim() || !userId.trim()) {
      setStatus({ message: 'Fill in API base, API key, and user ID.', kind: 'error' });
      return;
    }

    const { sample, error } = finalizeSample();
    if (error) {
      setStatus({ message: error, kind: 'error' });
      resetPasswordField();
      return;
    }

    const chunks = chunkSample(sample, chunkSize);
    if (chunks.length === 0) {
      setStatus({ message: 'No complete keystroke chunks captured.', kind: 'error' });
      resetPasswordField();
      return;
    }

    const sequence = await hashKeyCodeSequence(sample);
    const currentBaseline = sequenceBaselineRef.current;
    if (currentBaseline.hash && sequence.hash !== currentBaseline.hash) {
      setStatus({
        message: `Password key sequence mismatch: expected ${currentBaseline.length} keys, got ${sequence.length}.`,
        kind: 'error'
      });
      resetPasswordField();
      return;
    }
    if (!currentBaseline.hash) {
      sequenceBaselineRef.current = sequence;
      setSequenceBaseline(sequence);
    }

    setBusy(true);
    setStatus({
      message: `Submitting ${chunks.length} chunk${chunks.length === 1 ? '' : 's'}...`,
      kind: ''
    });

    try {
      const responses = [];
      for (const chunk of chunks) {
        const body = {
          external_user_id: chunkExternalUserId(chunk),
          raw_data: chunk.raw_data
        };
        const response = await callCadence('/v1/score', {
          ...body,
          threshold: Number(threshold),
          source: 'chunk_test',
          quality_score: sample.quality_score,
          flags: sample.flags || []
        });
        responses.push({ chunk, response });
      }

      const enrollmentRequired = Math.max(
        ...responses.map(({ response }) => response.enrollment_required ?? DEFAULT_ENROLLMENT_REQUIRED)
      );
      const completedAttempts = Math.min(
        ...responses.map(({ response }) =>
          Math.min(response.enrollment_count ?? 0, enrollmentRequired)
        )
      );
      enrollmentAttemptsRef.current = completedAttempts;
      setEnrollmentAttempts(completedAttempts);
      const backendEnrolled = responses.every(({ response }) => response.enrolled);
      const fullyEnrolled = backendEnrolled;
      setEnrollment({
        enrolled: fullyEnrolled,
        enrollment_count: completedAttempts,
        enrollment_required: enrollmentRequired,
        enrollment_samples_needed: Math.max(enrollmentRequired - completedAttempts, 0)
      });

      const scores = responses.map(({ response }) => response.score);
      const aggregate = aggregateScore(scores);
      const enrollmentSubmission = responses.some(({ response }) => response.reason === 'enrollment');
      const accepted = (
        !enrollmentSubmission &&
        fullyEnrolled &&
        typeof aggregate === 'number' &&
        aggregate >= Number(threshold)
      );
      const summary = {
        score: aggregate,
        accepted,
        reason: enrollmentSubmission
          ? 'enrollment'
          : (fullyEnrolled ? (accepted ? 'accepted' : 'low_confidence') : 'not_enrolled'),
        chunks: responses
      };

      setChunkResults(responses);
      if (!enrollmentSubmission) {
        setLastScore(summary);
      }

      const entry = {
        id: `${Date.now()}-${summary.reason}`,
        mode: enrollmentSubmission ? 'enroll' : 'score',
        keyCount: sampleKeyCount(sample),
        chunkCount: chunks.length,
        quality: sample.quality_score,
        response: summary,
        enrollment: {
          enrollment_count: completedAttempts,
          enrollment_required: enrollmentRequired
        }
      };
      setHistory((current) => [entry, ...current].slice(0, 8));

      if (enrollmentSubmission) {
        setStatus({
          message: `Backend used this as enrollment ${completedAttempts}/${enrollmentRequired}; ${chunks.length} chunk bucket${chunks.length === 1 ? '' : 's'} updated`,
          kind: fullyEnrolled ? 'success' : ''
        });
      } else {
        setStatus({
          message: `${accepted ? 'Accepted' : 'Rejected'} at ${formatScore(aggregate)} across ${chunks.length} chunks`,
          kind: accepted ? 'success' : 'error'
        });
      }
    } catch (error) {
      setStatus({ message: error.message, kind: 'error' });
    } finally {
      setBusy(false);
      resetPasswordField();
    }
  }, [
    apiBase,
    apiKey,
    userId,
    chunkSize,
    threshold,
    finalizeSample,
    callCadence,
    chunkExternalUserId,
    resetPasswordField
  ]);

  const handleSubmit = useCallback((event) => {
    event.preventDefault();
    submitSample();
  }, [submitSample]);

  const resetRun = useCallback(() => {
    setEnrollment(null);
    setEnrollmentAttempts(0);
    enrollmentAttemptsRef.current = 0;
    setLastSample(null);
    setLastScore(null);
    setChunkResults([]);
    setHistory([]);
    setRunId(randomId('run'));
    sequenceBaselineRef.current = { hash: '', length: 0 };
    setSequenceBaseline({ hash: '', length: 0 });
    setStatus({ message: 'Cleared local test output and key sequence baseline', kind: 'success' });
    resetPasswordField();
  }, [resetPasswordField]);

  const enrollmentPct = enrollment?.enrollment_required
    ? Math.min(100, (enrollment.enrollment_count / enrollment.enrollment_required) * 100)
    : 0;

  return (
    <main className="credential-test">
      <div className="bg-gradient" />
      <div className="bg-grid" />

      <section className="credential-shell">
        <header className="credential-head">
          <a className="brand" href="/">
            <span className="brand-mark">*</span>
            <span>Synergyze</span>
            <span className="brand-tld">/ Cadence test</span>
          </a>
          <h1>Chunked timing test</h1>
          <p>
            Submit overlapping password chunks; the backend enrolls until ready, then scores logins.
          </p>
        </header>

        <div className="credential-grid">
          <form className="credential-panel" onSubmit={handleSubmit}>
            <div className="field-grid">
              <label>
                <span>API base</span>
                <input
                  value={apiBase}
                  onChange={(event) => setApiBase(event.target.value)}
                  spellCheck="false"
                />
              </label>
              <label>
                <span>Cadence API key</span>
                <input
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  spellCheck="false"
                  type="password"
                />
              </label>
              <label>
                <span>User ID</span>
                <input
                  value={userId}
                  onChange={(event) => setUserId(event.target.value)}
                  spellCheck="false"
                />
              </label>
              <label>
                <span>Chunk size</span>
                <input
                  value={chunkSize}
                  onChange={(event) => setChunkSize(event.target.value)}
                  inputMode="numeric"
                />
              </label>
              <label>
                <span>Threshold</span>
                <input
                  value={threshold}
                  onChange={(event) => setThreshold(event.target.value)}
                  inputMode="decimal"
                />
              </label>
              <label>
                <span>Password field</span>
                <input
                  ref={passwordRef}
                  id="credential-test-password"
                  type="password"
                  autoComplete="new-password"
                  spellCheck="false"
                />
              </label>
            </div>

            <div className="credential-actions">
              <button className="btn btn-primary" type="submit" disabled={busy}>
                Submit
              </button>
              <button className="btn btn-ghost" type="button" disabled={busy} onClick={resetRun}>
                Clear
              </button>
            </div>

            <p className={`auth-meta${status.kind ? ` is-${status.kind}` : ''}`}>
              {status.message}
            </p>

            <div className="credential-meter" aria-label="Enrollment progress">
              <div style={{ width: `${enrollmentPct}%` }} />
            </div>
            <p className="credential-fineprint">
              {enrollment
                ? `${enrollment.enrollment_count}/${enrollment.enrollment_required} full enrollment attempts`
                : 'No chunk enrollment state loaded for this user'}
            </p>
          </form>

          <aside className="credential-panel credential-results">
            <div>
              <p className="aside-eyebrow">Aggregate score</p>
              <strong className={lastScore?.accepted ? 'score-good' : 'score-bad'}>
                {lastScore ? formatScore(lastScore.score) : 'No run'}
              </strong>
              <span>{lastScore ? lastScore.reason : 'Submit until enrollment is complete.'}</span>
            </div>

            <dl className="credential-stats">
              <div>
                <dt>Decision</dt>
                <dd>{lastScore ? (lastScore.accepted ? 'Accept' : 'Reject') : '-'}</dd>
              </div>
              <div>
                <dt>Keys</dt>
                <dd>{lastSample ? sampleKeyCount(lastSample) : '-'}</dd>
              </div>
              <div>
                <dt>Chunks</dt>
                <dd>{chunkResults.length || '-'}</dd>
              </div>
              <div>
                <dt>Quality</dt>
                <dd>
                  {lastSample ? `${Math.round((lastSample.quality_score || 0) * 100)}%` : '-'}
                </dd>
              </div>
              <div>
                <dt>User bucket</dt>
                <dd title={baseExternalUserId}>{baseExternalUserId}</dd>
              </div>
              <div>
                <dt>Sequence</dt>
                <dd>
                  {sequenceBaseline.hash
                    ? `${sequenceBaseline.length} keys locked`
                    : 'not set'}
                </dd>
              </div>
            </dl>
          </aside>
        </div>

        {chunkResults.length > 0 && (
          <section className="credential-history">
            {chunkResults.map(({ chunk, response }) => (
              <article key={`${chunk.index}-${chunk.start}`}>
                <div>
                  <strong>Chunk {chunk.index + 1}</strong>
                  <span>
                    keys {chunk.start + 1}-{chunk.end} - {chunk.raw_data.keystrokes.length} keys
                  </span>
                </div>
                <div>
                  {typeof response.score === 'number'
                    ? `${response.accepted ? 'Accept' : 'Reject'} - ${formatScore(response.score)}`
                    : `${Math.min(response.enrollment_count, response.enrollment_required)}/${response.enrollment_required}`}
                </div>
              </article>
            ))}
          </section>
        )}

        <section className="credential-history">
          {history.map((entry) => (
            <article key={entry.id}>
              <div>
                <strong>{entry.mode === 'enroll' ? 'Enroll' : 'Score'}</strong>
                <span>
                  {entry.keyCount} keys - {entry.chunkCount} chunks - quality {Math.round((entry.quality || 0) * 100)}%
                </span>
              </div>
              <div>
                {entry.mode === 'score'
                  ? `${entry.response.accepted ? 'Accept' : 'Reject'} - ${formatScore(entry.response.score)}`
                  : `${entry.enrollment.enrollment_count}/${entry.enrollment.enrollment_required}`}
              </div>
            </article>
          ))}
        </section>
      </section>
    </main>
  );
}
