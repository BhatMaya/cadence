import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { computeQualityScore, createCapture, LIBRARY_VERSION } from '../src/capture.js';
import type { CaptureEvent, Sample } from '../src/types.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * jsdom synthesizes events with `isTrusted === false` and the property
 * is non-configurable. The capture source exposes a tagged escape
 * hatch (`__cadenceTestTrusted`) specifically for tests — see the
 * comment on `isEventTrusted` in src/capture.ts.
 */
function dispatchTrusted(
  target: HTMLElement,
  type: 'keydown' | 'keyup',
  init: KeyboardEventInit
): KeyboardEvent {
  const ev = new KeyboardEvent(type, init);
  (ev as unknown as { __cadenceTestTrusted: boolean }).__cadenceTestTrusted = true;
  target.dispatchEvent(ev);
  return ev;
}

/** Like dispatchTrusted but with a forced ev.timeStamp. */
function dispatchAt(
  target: HTMLElement,
  type: 'keydown' | 'keyup',
  timeStamp: number,
  init: KeyboardEventInit
): KeyboardEvent {
  const ev = new KeyboardEvent(type, init);
  (ev as unknown as { __cadenceTestTrusted: boolean }).__cadenceTestTrusted = true;
  Object.defineProperty(ev, 'timeStamp', { value: timeStamp, configurable: true });
  target.dispatchEvent(ev);
  return ev;
}

function dispatchDrop(target: HTMLElement, types: string[]): void {
  const ev = new Event('drop');
  Object.defineProperty(ev, 'dataTransfer', {
    configurable: true,
    value: { types }
  });
  target.dispatchEvent(ev);
}

function setVisibility(state: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: state
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

function collect(cap: ReturnType<typeof createCapture>) {
  const events: CaptureEvent[] = [];
  cap.on('sample_ready', (e) => events.push(e));
  cap.on('sample_rejected', (e) => events.push(e));
  cap.on('error', (e) => events.push(e));
  return events;
}

function lastSample(events: CaptureEvent[]): Sample {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.type === 'sample_ready') return e.sample;
  }
  throw new Error('no sample_ready event in log');
}

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

let input: HTMLInputElement;

beforeEach(() => {
  input = document.createElement('input');
  document.body.appendChild(input);
});

afterEach(() => {
  input.remove();
  setVisibility('visible');
});

// ---------------------------------------------------------------------------
// Option validation (retained from Phase 1)
// ---------------------------------------------------------------------------

describe('createCapture — option validation', () => {
  it('rejects missing or non-element target', () => {
    // @ts-expect-error -- intentional bad input
    expect(() => createCapture({ mode: 'password' })).toThrow(/target/);
    // @ts-expect-error -- intentional bad input
    expect(() => createCapture({ target: {}, mode: 'password' })).toThrow(/target/);
  });

  it('rejects unknown mode', () => {
    // @ts-expect-error -- intentional bad input
    expect(() => createCapture({ target: input, mode: 'bogus' })).toThrow(/mode/);
  });
});

// ---------------------------------------------------------------------------
// Security invariant: does not read event.key
// ---------------------------------------------------------------------------

describe('createCapture — security', () => {
  it('does not read event.key (password-mode invariant)', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    cap.start();

    let keyRead = false;
    const ev = new KeyboardEvent('keydown', { code: 'KeyA' });
    (ev as unknown as { __cadenceTestTrusted: boolean }).__cadenceTestTrusted = true;
    Object.defineProperty(ev, 'key', {
      get() {
        keyRead = true;
        return '';
      },
      configurable: true
    });
    input.dispatchEvent(ev);

    expect(keyRead).toBe(false);
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// State machine (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — state machine', () => {
  it('start() while CAPTURING emits error and does not duplicate listeners', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    cap.start();

    const errs = log.filter((e) => e.type === 'error');
    expect(errs).toHaveLength(1);
    expect((errs[0] as Extract<CaptureEvent, { type: 'error' }>).error.message).toMatch(
      /already started/
    );

    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    expect(lastSample(log).events).toHaveLength(2);
    cap.destroy();
  });

  it('stop() in IDLE emits sample_rejected with session_not_started', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.stop();
    expect(log).toEqual([
      { type: 'sample_rejected', reason: 'session_not_started' }
    ]);
    cap.destroy();
  });

  it('stop() after DESTROYED is a silent no-op', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    cap.destroy();
    const log: CaptureEvent[] = [];
    cap.on('sample_rejected', (e) => log.push(e));
    cap.on('error', (e) => log.push(e));
    cap.stop();
    expect(log).toHaveLength(0);
  });

  it('destroy() while CAPTURING detaches listeners; no further events processed', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    cap.destroy();
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    dispatchTrusted(input, 'keydown', { code: 'KeyB' });
    expect(() => cap.destroy()).not.toThrow();
  });

  it('start() after stop() begins a clean session', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);

    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyB' });
    dispatchTrusted(input, 'keyup', { code: 'KeyB' });
    cap.stop();

    const ready = log.filter((e) => e.type === 'sample_ready') as Extract<
      CaptureEvent,
      { type: 'sample_ready' }
    >[];
    expect(ready).toHaveLength(2);
    expect(ready[0]!.sample.events.map((e) => e.code)).toEqual(['KeyA', 'KeyA']);
    expect(ready[1]!.sample.events.map((e) => e.code)).toEqual(['KeyB', 'KeyB']);
    expect(ready[0]!.sample.session_id).not.toEqual(ready[1]!.sample.session_id);

    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Pressed-key pairing (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — pressed-key pairing', () => {
  it('drops OS auto-repeat (repeated keydown without intervening keyup)', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    const sample = lastSample(log);
    expect(sample.events.map((e) => e.type)).toEqual(['down', 'up']);
    cap.destroy();
  });

  it('records orphan keyup (no prior keydown)', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyB' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    const sample = lastSample(log);
    expect(sample.events.map((e) => `${e.type}:${e.code}`)).toEqual([
      'down:KeyA',
      'up:KeyB',
      'up:KeyA'
    ]);
    cap.destroy();
  });

  it('records a normal down/up pair', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyZ' });
    dispatchTrusted(input, 'keyup', { code: 'KeyZ' });
    cap.stop();

    const sample = lastSample(log);
    expect(sample.events).toHaveLength(2);
    expect(sample.events[0]).toMatchObject({ type: 'down', code: 'KeyZ' });
    expect(sample.events[1]).toMatchObject({ type: 'up', code: 'KeyZ' });
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// isTrusted filter (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — isTrusted filter', () => {
  it('drops untrusted events and increments untrusted_events counter', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();

    input.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyA' }));
    input.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyA' }));

    dispatchTrusted(input, 'keydown', { code: 'KeyB' });
    dispatchTrusted(input, 'keyup', { code: 'KeyB' });

    cap.stop();

    const sample = lastSample(log);
    expect(sample.events.map((e) => e.code)).toEqual(['KeyB', 'KeyB']);
    expect(sample.untrusted_events).toBe(2);
    cap.destroy();
  });

  it('untrusted keydown does not populate the pressed-key map', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();

    input.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyA' }));
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });

    cap.stop();
    const sample = lastSample(log);
    expect(sample.events.map((e) => e.type)).toEqual(['down', 'up']);
    expect(sample.untrusted_events).toBe(1);
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Timestamp rebase (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — timestamp rebase', () => {
  it('emits session-relative, non-negative, monotonic t values', () => {
    const spy = vi.spyOn(performance, 'now').mockImplementation(() => 1000);

    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);

    cap.start();
    dispatchAt(input, 'keydown', 1050, { code: 'KeyA' });
    dispatchAt(input, 'keyup', 1125, { code: 'KeyA' });
    dispatchAt(input, 'keydown', 1200, { code: 'KeyB' });
    dispatchAt(input, 'keyup', 1275, { code: 'KeyB' });
    cap.stop();

    const sample = lastSample(log);
    expect(sample.events.map((e) => e.t)).toEqual([50, 125, 200, 275]);
    for (const e of sample.events) expect(e.t).toBeGreaterThanOrEqual(0);

    cap.destroy();
    spy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Validation (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — validation on stop()', () => {
  it('rejects empty_sample when no events recorded', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'empty_sample' });
    cap.destroy();
  });

  it('rejects below_min_length when keydown count < minLength', () => {
    const cap = createCapture({ target: input, mode: 'password', minLength: 3 });
    const log = collect(cap);
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    dispatchTrusted(input, 'keydown', { code: 'KeyB' });
    dispatchTrusted(input, 'keyup', { code: 'KeyB' });
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'below_min_length' });
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Buffer clearing (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — buffer clearing', () => {
  it('does not bleed events, flags, untrusted count, or pressed keys between sessions', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);

    cap.start();
    input.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyX' })); // untrusted
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    input.dispatchEvent(new Event('blur')); // flag
    cap.stop();

    const s1 = (log.filter((e) => e.type === 'sample_ready') as Extract<
      CaptureEvent,
      { type: 'sample_ready' }
    >[])[0]!.sample;
    expect(s1.events).toHaveLength(2);
    expect(s1.untrusted_events).toBe(1);
    expect(s1.flags).toContain('focus_lost');

    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    const s2 = (log.filter((e) => e.type === 'sample_ready') as Extract<
      CaptureEvent,
      { type: 'sample_ready' }
    >[])[1]!.sample;
    expect(s2.events).toHaveLength(2);
    expect(s2.untrusted_events).toBe(0);
    expect(s2.flags).toEqual([]);
    expect(s2.session_id).not.toEqual(s1.session_id);

    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Sample construction (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — sample construction', () => {
  it('populates every field of the Sample schema', () => {
    input.id = 'login-pw';
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();

    const sample = lastSample(log);
    expect(sample.session_id).toMatch(/^\d+-[a-z0-9]+$/);
    expect(sample.field_id).toBe('login-pw');
    expect(sample.session_start_ms).toBeGreaterThan(0);
    expect(sample.events).toHaveLength(2);
    expect(sample.flags).toEqual([]);
    expect(sample.untrusted_events).toBe(0);
    expect(sample.env.user_agent).toEqual(navigator.userAgent);
    expect(sample.env.timing_resolution_ms).toBeGreaterThan(0);
    expect(sample.quality_score).toBeGreaterThanOrEqual(0);
    expect(sample.quality_score).toBeLessThanOrEqual(1);
    expect(sample.library_version).toBe(LIBRARY_VERSION);

    cap.destroy();
  });

  it('invokes onSample callback on sample_ready', () => {
    const onSample = vi.fn();
    const cap = createCapture({ target: input, mode: 'password', onSample });
    cap.start();
    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });
    cap.stop();
    expect(onSample).toHaveBeenCalledTimes(1);
    expect(onSample.mock.calls[0]![0].events).toHaveLength(2);
    cap.destroy();
  });

  it('does NOT invoke onSample on sample_rejected', () => {
    const onSample = vi.fn();
    const cap = createCapture({ target: input, mode: 'password', onSample });
    cap.start();
    cap.stop();
    expect(onSample).not.toHaveBeenCalled();
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Subscription management (Phase 2)
// ---------------------------------------------------------------------------

describe('createCapture — subscription', () => {
  it('on() returns an idempotent unsubscribe', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const handler = vi.fn();
    const off = cap.on('sample_rejected', handler);

    cap.stop();
    expect(handler).toHaveBeenCalledTimes(1);

    off();
    off();

    cap.stop();
    expect(handler).toHaveBeenCalledTimes(1);

    cap.destroy();
  });

  it('destroy() clears all subscribers', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const handler = vi.fn();
    cap.on('sample_rejected', handler);
    cap.destroy();
    cap.stop();
    expect(handler).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// Phase 4 — element validity
// ===========================================================================

describe('element validity', () => {
  it('disconnected target: start() emits error, state stays IDLE', () => {
    const orphan = document.createElement('input'); // not appended
    const cap = createCapture({ target: orphan, mode: 'password' });
    const log = collect(cap);
    cap.start();
    const err = log.find((e) => e.type === 'error') as
      | Extract<CaptureEvent, { type: 'error' }>
      | undefined;
    expect(err?.error.message).toMatch(/not in the document/);

    // IDLE confirmation: stop() must emit session_not_started.
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'session_not_started' });
    cap.destroy();
  });

  it('readonly target: start() emits error, state stays IDLE', () => {
    input.readOnly = true;
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    const err = log.find((e) => e.type === 'error') as
      | Extract<CaptureEvent, { type: 'error' }>
      | undefined;
    expect(err?.error.message).toMatch(/read-only/);

    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'session_not_started' });
    cap.destroy();
  });

  it('display:none target: start() emits error', () => {
    input.style.display = 'none';
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    const err = log.find((e) => e.type === 'error') as
      | Extract<CaptureEvent, { type: 'error' }>
      | undefined;
    expect(err?.error.message).toMatch(/not visible/);
    cap.destroy();
  });

  it('position:fixed target: start() succeeds (offsetParent null is not hidden)', () => {
    input.style.position = 'fixed';
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    cap.stop();

    const ready = log.find((e) => e.type === 'sample_ready') as
      | Extract<CaptureEvent, { type: 'sample_ready' }>
      | undefined;
    expect(ready).toBeTruthy();
    expect(ready!.sample.events).toHaveLength(2);
    cap.destroy();
  });
});

// ===========================================================================
// Phase 4 — quality scoring (computeQualityScore directly)
// ===========================================================================

describe('computeQualityScore', () => {
  const perfectEvents = (n: number): Array<{ type: 'down' | 'up'; code: string }> => {
    const out: Array<{ type: 'down' | 'up'; code: string }> = [];
    for (let i = 0; i < n; i++) {
      out.push({ type: 'down', code: `Key${i}` });
      out.push({ type: 'up', code: `Key${i}` });
    }
    return out;
  };

  it('perfect session (20 pairs, no flags, high resolution) → 1.0', () => {
    expect(computeQualityScore(perfectEvents(20), new Set(), 0.05)).toBe(1.0);
  });

  it('single flag → ≤ 0.7', () => {
    const s = computeQualityScore(perfectEvents(20), new Set(['paste_detected']), 0.05);
    expect(s).toBeLessThanOrEqual(0.7);
    expect(s).toBeCloseTo(0.7, 5);
  });

  it('half keydowns unpaired → ≤ 0.5', () => {
    // 10 downs, 5 matched ups. completeness=0.5, length=10/20=0.5.
    const events: Array<{ type: 'down' | 'up'; code: string }> = [];
    for (let i = 0; i < 10; i++) events.push({ type: 'down', code: `Key${i}` });
    for (let i = 0; i < 5; i++) events.push({ type: 'up', code: `Key${i}` });
    const s = computeQualityScore(events, new Set(), 0.05);
    expect(s).toBeLessThanOrEqual(0.5);
    expect(s).toBeCloseTo(0.25, 5);
  });

  it('5 keydowns → ≤ 0.25 (length factor)', () => {
    const s = computeQualityScore(perfectEvents(5), new Set(), 0.05);
    expect(s).toBeLessThanOrEqual(0.25);
    expect(s).toBeCloseTo(0.25, 5);
  });

  it('clamps into [0, 1]', () => {
    // 4 flags: penalty = min(0.8, 1.2) = 0.8. Everything else perfect.
    const s = computeQualityScore(
      perfectEvents(20),
      new Set(['a', 'b', 'c', 'd']),
      0.05
    );
    expect(s).toBeGreaterThanOrEqual(0);
    expect(s).toBeLessThanOrEqual(1);
    expect(s).toBeCloseTo(0.2, 5);
  });

  it('empty events returns 0 without crashing', () => {
    expect(computeQualityScore([], new Set(), 0.05)).toBe(0);
    expect(computeQualityScore([], new Set(['a']), 1000)).toBe(0);
  });

  it('coarse timing resolution reduces timing_factor', () => {
    // Resolution 5ms → timing_factor = 0.3.
    const s = computeQualityScore(perfectEvents(20), new Set(), 5);
    expect(s).toBeCloseTo(0.3, 5);
  });
});

// ===========================================================================
// Phase 3 — contamination detection
// ===========================================================================

// Each contamination test typically dispatches a valid keypair (so the
// sample isn't rejected as empty/below_min) plus the contamination
// signal. password/username reject; freetext emits with flag.

function typeOneKey(target: HTMLElement): void {
  dispatchTrusted(target, 'keydown', { code: 'KeyA' });
  dispatchTrusted(target, 'keyup', { code: 'KeyA' });
}

describe('contamination — paste', () => {
  it('paste event: rejected in password mode', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('paste'));
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'poisoned' });
    cap.destroy();
  });

  it('paste event: flag recorded in freetext mode, sample emitted', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('paste'));
    cap.stop();
    const s = lastSample(log);
    expect(s.flags).toContain('paste_detected');
    cap.destroy();
  });

  it('drop with text data: flag raised', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    dispatchDrop(input, ['text/plain']);
    cap.stop();
    expect(lastSample(log).flags).toContain('paste_detected');
    cap.destroy();
  });

  it('drop without text data: no flag', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    dispatchDrop(input, ['application/octet-stream']);
    cap.stop();
    expect(lastSample(log).flags).not.toContain('paste_detected');
    cap.destroy();
  });
});

describe('contamination — focus loss', () => {
  it('blur on target raises focus_lost', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('blur'));
    cap.stop();
    expect(lastSample(log).flags).toContain('focus_lost');
    cap.destroy();
  });

  it('visibilitychange to hidden raises focus_lost', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    setVisibility('hidden');
    cap.stop();
    expect(lastSample(log).flags).toContain('focus_lost');
    cap.destroy();
  });

  it('visibilitychange back to visible does NOT raise focus_lost', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    // Ensure we're visible, then dispatch visibilitychange without
    // going hidden. Should be a no-op for flags.
    setVisibility('visible');
    cap.stop();
    expect(lastSample(log).flags).not.toContain('focus_lost');
    cap.destroy();
  });

  it('blur rejects in password mode', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('blur'));
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'poisoned' });
    cap.destroy();
  });
});

describe('contamination — inter-key pause', () => {
  it('gap > maxInterKeyPauseMs raises inter_key_pause_exceeded on next event', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    dispatchAt(input, 'keydown', 1000, { code: 'KeyA' });
    dispatchAt(input, 'keyup', 1100, { code: 'KeyA' });
    // Gap of 6001ms > default 5000; flag must be raised when the next
    // event arrives, not before.
    dispatchAt(input, 'keydown', 7101, { code: 'KeyB' });
    dispatchAt(input, 'keyup', 7200, { code: 'KeyB' });
    cap.stop();
    expect(lastSample(log).flags).toContain('inter_key_pause_exceeded');
    cap.destroy();
  });

  it('gap <= maxInterKeyPauseMs does NOT raise the flag', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    dispatchAt(input, 'keydown', 1000, { code: 'KeyA' });
    dispatchAt(input, 'keyup', 1100, { code: 'KeyA' });
    dispatchAt(input, 'keydown', 2000, { code: 'KeyB' });
    dispatchAt(input, 'keyup', 2100, { code: 'KeyB' });
    cap.stop();
    expect(lastSample(log).flags).not.toContain('inter_key_pause_exceeded');
    cap.destroy();
  });

  it('respects custom maxInterKeyPauseMs option', () => {
    const cap = createCapture({
      target: input,
      mode: 'freetext',
      maxInterKeyPauseMs: 100
    });
    const log = collect(cap);
    cap.start();
    dispatchAt(input, 'keydown', 1000, { code: 'KeyA' });
    dispatchAt(input, 'keyup', 1050, { code: 'KeyA' });
    dispatchAt(input, 'keydown', 1200, { code: 'KeyB' }); // gap = 150 > 100
    cap.stop();
    expect(lastSample(log).flags).toContain('inter_key_pause_exceeded');
    cap.destroy();
  });
});

describe('contamination — IME composition', () => {
  it('drops keydown/keyup during composition and raises ime_active', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();

    dispatchTrusted(input, 'keydown', { code: 'KeyA' });
    dispatchTrusted(input, 'keyup', { code: 'KeyA' });

    input.dispatchEvent(new CompositionEvent('compositionstart'));
    dispatchTrusted(input, 'keydown', { code: 'KeyB' }); // dropped
    dispatchTrusted(input, 'keyup', { code: 'KeyB' });   // dropped
    input.dispatchEvent(new CompositionEvent('compositionend'));

    dispatchTrusted(input, 'keydown', { code: 'KeyC' });
    dispatchTrusted(input, 'keyup', { code: 'KeyC' });

    cap.stop();
    const s = lastSample(log);
    expect(s.events.map((e) => e.code)).toEqual(['KeyA', 'KeyA', 'KeyC', 'KeyC']);
    expect(s.flags).toContain('ime_active');
    cap.destroy();
  });

  it('multiple compositionstart events raise ime_active only once', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new CompositionEvent('compositionstart'));
    input.dispatchEvent(new CompositionEvent('compositionend'));
    input.dispatchEvent(new CompositionEvent('compositionstart'));
    input.dispatchEvent(new CompositionEvent('compositionend'));
    cap.stop();
    const s = lastSample(log);
    expect(s.flags.filter((f) => f === 'ime_active')).toHaveLength(1);
    cap.destroy();
  });
});

describe('contamination — autofill', () => {
  it('input event without recent keydown raises autofill_suspected', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    input.dispatchEvent(new Event('input'));
    // Add a real keypair so the sample isn't empty/below_min.
    typeOneKey(input);
    cap.stop();
    expect(lastSample(log).flags).toContain('autofill_suspected');
    cap.destroy();
  });

  it('input event immediately after a keydown does NOT raise the flag', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    // Dispatch keydown and input with the same timeStamp (simulating
    // the same user action).
    const kd = new KeyboardEvent('keydown', { code: 'KeyA' });
    (kd as unknown as { __cadenceTestTrusted: boolean }).__cadenceTestTrusted = true;
    Object.defineProperty(kd, 'timeStamp', { value: 5000, configurable: true });
    input.dispatchEvent(kd);
    const inEv = new Event('input');
    Object.defineProperty(inEv, 'timeStamp', { value: 5010, configurable: true });
    input.dispatchEvent(inEv);
    dispatchAt(input, 'keyup', 5020, { code: 'KeyA' });
    cap.stop();
    expect(lastSample(log).flags).not.toContain('autofill_suspected');
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Mode behavior and flag idempotency
// ---------------------------------------------------------------------------

describe('contamination — mode behavior', () => {
  it('password mode rejects on any flag', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('paste'));
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'poisoned' });
    cap.destroy();
  });

  it('username mode rejects on any flag (Phase 3: same as password)', () => {
    const cap = createCapture({ target: input, mode: 'username' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('blur'));
    cap.stop();
    expect(log).toContainEqual({ type: 'sample_rejected', reason: 'poisoned' });
    cap.destroy();
  });

  it('freetext mode emits sample_ready with flags recorded', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('paste'));
    input.dispatchEvent(new Event('blur'));
    cap.stop();
    const s = lastSample(log);
    expect(s.flags.sort()).toEqual(['focus_lost', 'paste_detected']);
    cap.destroy();
  });
});

describe('contamination — flag idempotency', () => {
  it('multiple blur events record focus_lost exactly once', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);
    cap.start();
    typeOneKey(input);
    input.dispatchEvent(new Event('blur'));
    input.dispatchEvent(new Event('blur'));
    input.dispatchEvent(new Event('blur'));
    cap.stop();
    const s = lastSample(log);
    expect(s.flags.filter((f) => f === 'focus_lost')).toHaveLength(1);
    cap.destroy();
  });
});

// ---------------------------------------------------------------------------
// Contamination listener cleanup
// ---------------------------------------------------------------------------

describe('contamination — listener cleanup', () => {
  it('destroy() removes the document-level visibilitychange listener', () => {
    // Explicit coverage for the only non-target-scoped listener.
    const cap = createCapture({ target: input, mode: 'freetext' });
    cap.start();
    cap.destroy();
    expect(() => setVisibility('hidden')).not.toThrow();
    // A second capture instance must not observe the first instance's
    // prior listener. Construct, start, stop without touching
    // visibility, and verify no focus_lost flag leaked in.
    const cap2 = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap2);
    cap2.start();
    typeOneKey(input);
    cap2.stop();
    const ready = log.find((e) => e.type === 'sample_ready') as
      | Extract<CaptureEvent, { type: 'sample_ready' }>
      | undefined;
    expect(ready?.sample.flags).toEqual([]);
    cap2.destroy();
  });

  it('after destroy(), contamination events on target do not crash or affect state', () => {
    const cap = createCapture({ target: input, mode: 'password' });
    cap.start();
    cap.destroy();

    expect(() => {
      input.dispatchEvent(new Event('paste'));
      dispatchDrop(input, ['text/plain']);
      input.dispatchEvent(new Event('blur'));
      input.dispatchEvent(new CompositionEvent('compositionstart'));
      input.dispatchEvent(new CompositionEvent('compositionend'));
      input.dispatchEvent(new Event('input'));
      setVisibility('hidden');
    }).not.toThrow();
  });

  it('after stop(), contamination events do not leak into the next session', () => {
    const cap = createCapture({ target: input, mode: 'freetext' });
    const log = collect(cap);

    cap.start();
    typeOneKey(input);
    cap.stop();

    // Between sessions, listeners are detached. Dispatched events are
    // ignored and cannot contribute flags to the next session.
    input.dispatchEvent(new Event('paste'));
    input.dispatchEvent(new Event('blur'));
    setVisibility('hidden');
    setVisibility('visible');

    cap.start();
    typeOneKey(input);
    cap.stop();

    const ready = log.filter((e) => e.type === 'sample_ready') as Extract<
      CaptureEvent,
      { type: 'sample_ready' }
    >[];
    expect(ready[1]!.sample.flags).toEqual([]);
    cap.destroy();
  });
});
