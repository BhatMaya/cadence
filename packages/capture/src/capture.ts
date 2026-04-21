import type {
  Capture,
  CaptureEvent,
  CaptureOptions,
  Sample,
  SampleKeyEvent
} from './types.js';

export const LIBRARY_VERSION = '0.1.0';

// No COMPLETE state: after stop() emits a sample, state returns to
// IDLE and the instance is reusable for a fresh session until destroy().
type State = 'IDLE' | 'CAPTURING' | 'POISONED' | 'DESTROYED';

interface RawEvent {
  type: 'down' | 'up';
  code: string;
  t_raw: number;
}

const DEFAULT_MAX_INTER_KEY_PAUSE_MS = 5000;
// An `input` event fired more than this after the last keydown looks
// like autofill rather than a byproduct of typing.
const AUTOFILL_QUIESCENCE_MS = 50;

/**
 * Create a keystroke-dynamics capture session bound to a DOM element.
 *
 * Subscribe to events before calling `start()` — errors emitted
 * synchronously from `start()` will otherwise be missed.
 */
export function createCapture(options: CaptureOptions): Capture {
  validateOptions(options);

  const subscribers = createEmitter<CaptureEvent>();
  const minLength = options.minLength ?? 1;
  const maxInterKeyPauseMs = options.maxInterKeyPauseMs ?? DEFAULT_MAX_INTER_KEY_PAUSE_MS;
  const fieldId =
    options.fieldId
    || options.target.id
    || options.target.getAttribute('name')
    || '';

  let state: State = 'IDLE';
  let sessionId = '';
  let sessionStartMs = 0;
  let monotonicRef = 0;
  let timingResolutionMs = 0;
  let events: RawEvent[] = [];
  const pressed = new Map<string, number>();
  let untrusted = 0;
  const flags = new Set<string>();
  let isComposing = false;
  let lastKeyDownTime = 0;

  const isLive = (): boolean => state === 'CAPTURING' || state === 'POISONED';

  const raiseFlag = (name: string): void => {
    flags.add(name);
    if (state === 'CAPTURING') state = 'POISONED';
  };

  const checkInterKeyPause = (ts: number): void => {
    if (events.length === 0) return;
    const lastT = events[events.length - 1]!.t_raw;
    if (ts - lastT > maxInterKeyPauseMs) raiseFlag('inter_key_pause_exceeded');
  };

  const onKeyDown = (ev: KeyboardEvent): void => {
    if (!isLive()) return;
    // event.isTrusted === false: synthetic input (scripts, testing
    // helpers, accessibility overlays). Don't record, but count so the
    // downstream pipeline knows the session saw contamination.
    if (!isEventTrusted(ev)) { untrusted++; return; }
    // During IME composition, key events aren't meaningful timing
    // signals — the user is operating the IME, not pressing characters
    // directly. Drop them from the log, but still count them as
    // activity so the autofill heuristic doesn't false-positive on
    // the post-composition `input` event.
    if (isComposing) { lastKeyDownTime = ev.timeStamp; return; }

    checkInterKeyPause(ev.timeStamp);
    lastKeyDownTime = ev.timeStamp;

    const code = ev.code;
    // Drop OS-level auto-repeat. The pressed map is authoritative;
    // event.repeat is inconsistent across platforms.
    if (pressed.has(code)) return;
    // ev.timeStamp reflects event fire time (not handler dispatch
    // time), which is more accurate under main-thread load.
    const t = ev.timeStamp;
    pressed.set(code, t);
    events.push({ type: 'down', code, t_raw: t });
  };

  const onKeyUp = (ev: KeyboardEvent): void => {
    if (!isLive()) return;
    if (!isEventTrusted(ev)) { untrusted++; return; }
    if (isComposing) return;

    checkInterKeyPause(ev.timeStamp);

    const code = ev.code;
    const t = ev.timeStamp;
    // Orphan keyups (no matching keydown) are still recorded — the
    // model may want to see them.
    pressed.delete(code);
    events.push({ type: 'up', code, t_raw: t });
  };

  const onPaste = (): void => {
    if (!isLive()) return;
    raiseFlag('paste_detected');
  };

  const onDrop = (ev: Event): void => {
    if (!isLive()) return;
    const dt = (ev as DragEvent).dataTransfer;
    if (!dt) return;
    const types = Array.from(dt.types ?? []);
    if (types.includes('text/plain') || types.includes('text/html')) {
      raiseFlag('paste_detected');
    }
  };

  const onBlur = (): void => {
    if (!isLive()) return;
    raiseFlag('focus_lost');
  };

  const onVisibilityChange = (): void => {
    if (!isLive()) return;
    if (document.visibilityState === 'hidden') raiseFlag('focus_lost');
  };

  const onCompositionStart = (): void => {
    if (!isLive()) return;
    isComposing = true;
    raiseFlag('ime_active');
  };

  const onCompositionEnd = (): void => {
    isComposing = false;
  };

  const onInput = (ev: Event): void => {
    if (!isLive()) return;
    // Heuristic: an input event with no recent keydown is autofill (or
    // context-menu paste, which we don't otherwise detect). Err toward
    // false negatives: require *no* keydown activity within the
    // quiescence window.
    //
    // This catches *simple* autofill (browser populates the field with
    // no keystrokes). It does NOT catch password managers that
    // simulate typing by synthesizing keydown events — those would
    // generally fail the `isTrusted` filter and surface as an elevated
    // `untrusted_events` count instead. The flag means "saw input
    // without a preceding keydown", not "detected all non-human input".
    if (
      lastKeyDownTime === 0 ||
      ev.timeStamp - lastKeyDownTime > AUTOFILL_QUIESCENCE_MS
    ) {
      raiseFlag('autofill_suspected');
    }
  };

  const listenerOpts: AddEventListenerOptions = { capture: true };
  let listenersAttached = false;

  const attachListeners = (): void => {
    if (listenersAttached) return;
    const t = options.target;
    t.addEventListener('keydown', onKeyDown, listenerOpts);
    t.addEventListener('keyup', onKeyUp, listenerOpts);
    t.addEventListener('paste', onPaste, listenerOpts);
    t.addEventListener('drop', onDrop, listenerOpts);
    t.addEventListener('blur', onBlur, listenerOpts);
    t.addEventListener('compositionstart', onCompositionStart, listenerOpts);
    t.addEventListener('compositionend', onCompositionEnd, listenerOpts);
    t.addEventListener('input', onInput, listenerOpts);
    document.addEventListener('visibilitychange', onVisibilityChange);
    listenersAttached = true;
  };

  const detachListeners = (): void => {
    if (!listenersAttached) return;
    const t = options.target;
    t.removeEventListener('keydown', onKeyDown, listenerOpts);
    t.removeEventListener('keyup', onKeyUp, listenerOpts);
    t.removeEventListener('paste', onPaste, listenerOpts);
    t.removeEventListener('drop', onDrop, listenerOpts);
    t.removeEventListener('blur', onBlur, listenerOpts);
    t.removeEventListener('compositionstart', onCompositionStart, listenerOpts);
    t.removeEventListener('compositionend', onCompositionEnd, listenerOpts);
    t.removeEventListener('input', onInput, listenerOpts);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    listenersAttached = false;
  };

  const resetBuffers = (): void => {
    events = [];
    pressed.clear();
    untrusted = 0;
    flags.clear();
    isComposing = false;
    lastKeyDownTime = 0;
  };

  const finalize = (): CaptureEvent => {
    if (events.length === 0) {
      return { type: 'sample_rejected', reason: 'empty_sample' };
    }
    let keydownCount = 0;
    for (const e of events) if (e.type === 'down') keydownCount++;
    if (keydownCount < minLength) {
      return { type: 'sample_rejected', reason: 'below_min_length' };
    }
    // password and username reject on contamination; freetext tolerates.
    if (state === 'POISONED' && options.mode !== 'freetext') {
      return { type: 'sample_rejected', reason: 'poisoned' };
    }

    const sampleEvents: SampleKeyEvent[] = events.map((e) => ({
      type: e.type,
      code: e.code,
      t: e.t_raw - monotonicRef
    }));
    const sample: Sample = {
      session_id: sessionId,
      field_id: fieldId,
      session_start_ms: sessionStartMs,
      events: sampleEvents,
      flags: Array.from(flags),
      untrusted_events: untrusted,
      env: {
        user_agent: navigator.userAgent,
        timing_resolution_ms: timingResolutionMs
      },
      quality_score: computeQualityScore(events, flags, timingResolutionMs),
      library_version: LIBRARY_VERSION
    };
    return { type: 'sample_ready', sample };
  };

  return {
    start() {
      if (state === 'DESTROYED') {
        subscribers.emit({
          type: 'error',
          error: new Error('createCapture: instance has been destroyed')
        });
        return;
      }
      if (state !== 'IDLE') {
        subscribers.emit({
          type: 'error',
          error: new Error('createCapture: session already started')
        });
        return;
      }
      // Element validity runs BEFORE any session state is captured so a
      // failed start() leaves the instance exactly as it was (state
      // IDLE, no buffers written, no listeners attached).
      const validityError = checkElementValidity(options.target);
      if (validityError) {
        subscribers.emit({ type: 'error', error: new Error(validityError) });
        return;
      }
      sessionId = generateSessionId();
      sessionStartMs = Date.now();
      timingResolutionMs = probeTimingResolution();
      monotonicRef = performance.now();
      state = 'CAPTURING';
      attachListeners();
    },

    stop() {
      if (state === 'DESTROYED') return;
      if (state === 'IDLE') {
        subscribers.emit({ type: 'sample_rejected', reason: 'session_not_started' });
        return;
      }
      detachListeners();
      const result = finalize();
      if (result.type === 'sample_ready' && options.onSample) {
        options.onSample(result.sample);
      }
      subscribers.emit(result);
      resetBuffers();
      state = 'IDLE';
    },

    destroy() {
      if (state === 'DESTROYED') return;
      detachListeners();
      resetBuffers();
      subscribers.clear();
      state = 'DESTROYED';
    },

    on(event, handler) {
      return subscribers.on(event, handler);
    }
  };
}

function validateOptions(options: CaptureOptions): void {
  if (!options || typeof options !== 'object') {
    throw new TypeError('createCapture: options is required');
  }
  if (
    !options.target ||
    typeof (options.target as HTMLElement).addEventListener !== 'function'
  ) {
    throw new TypeError('createCapture: options.target must be an HTMLElement');
  }
  if (
    options.mode !== 'password' &&
    options.mode !== 'username' &&
    options.mode !== 'freetext'
  ) {
    throw new TypeError(
      `createCapture: options.mode must be "password" | "username" | "freetext", got ${String(options.mode)}`
    );
  }
}

/**
 * TEST-ONLY escape hatch for the `isTrusted` filter.
 *
 * jsdom dispatches synthetic events with `isTrusted === false` and the
 * property is non-configurable, so tests cannot forge it. A tagged
 * property on the event lets unit tests exercise the recording path
 * without disabling the real filter in production code. Production
 * events from real user input have `isTrusted === true` and are
 * unaffected.
 *
 * Do NOT set this tag in production code. It is a defense bypass.
 */
function isEventTrusted(ev: KeyboardEvent): boolean {
  if (ev.isTrusted) return true;
  return (ev as unknown as { __cadenceTestTrusted?: boolean }).__cadenceTestTrusted === true;
}

function checkElementValidity(el: HTMLElement): string | null {
  if (!el.isConnected) return 'target is not in the document';
  if ((el as HTMLInputElement).readOnly === true) return 'target is read-only';
  // offsetParent alone is a misleading signal: `position: fixed`
  // elements also report null. Require display === 'none' to confirm
  // the element is actually hidden.
  if (el.offsetParent === null && getComputedStyle(el).display === 'none') {
    return 'target is not visible';
  }
  return null;
}

/**
 * Compute a 0–1 quality score for a captured session. Pure function;
 * exported for direct unit testing but not re-exported from the
 * package entry point.
 */
export function computeQualityScore(
  events: ReadonlyArray<{ type: 'down' | 'up'; code: string }>,
  flags: ReadonlySet<string>,
  timing_resolution_ms: number
): number {
  // Clock precision. High-resolution timers support meaningful hold
  // and flight-time features; coarse clocks (1ms+) wash them out.
  const timing_factor =
    timing_resolution_ms <= 0.1 ? 1.0 :
    timing_resolution_ms <= 1.0 ? 0.7 :
    0.3;

  let keydownCount = 0;
  for (const e of events) if (e.type === 'down') keydownCount++;

  // Pairing completeness. Unmatched keydowns (stuck keys, session
  // ended mid-press) reduce confidence that each timing feature is
  // real.
  const matchedPairs = countMatchedPairs(events);
  const completeness = keydownCount > 0 ? matchedPairs / keydownCount : 0;

  // Sample length. Short samples expose less inter-key timing; the
  // factor saturates at 20 keydowns.
  const length_factor = Math.min(1.0, keydownCount / 20);

  // Contamination. Each distinct flag costs 0.3, capped so a heavily
  // flagged sample retains some signal instead of zeroing out.
  const contamination_penalty = Math.min(0.8, flags.size * 0.3);

  return Math.max(
    0,
    Math.min(
      1,
      timing_factor * completeness * length_factor * (1 - contamination_penalty)
    )
  );
}

function countMatchedPairs(
  events: ReadonlyArray<{ type: 'down' | 'up'; code: string }>
): number {
  let matched = 0;
  const open = new Map<string, number>();
  for (const e of events) {
    if (e.type === 'down') {
      open.set(e.code, (open.get(e.code) ?? 0) + 1);
    } else {
      const count = open.get(e.code) ?? 0;
      if (count > 0) {
        open.set(e.code, count - 1);
        matched++;
      }
    }
  }
  return matched;
}

function generateSessionId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function probeTimingResolution(): number {
  let min = Number.POSITIVE_INFINITY;
  let last = performance.now();
  for (let i = 0; i < 100; i++) {
    const t = performance.now();
    const d = t - last;
    if (d > 0 && d < min) min = d;
    last = t;
  }
  return Number.isFinite(min) ? min : 1;
}

interface Emitter<E extends { type: string }> {
  on<K extends E['type']>(
    event: K,
    handler: (payload: Extract<E, { type: K }>) => void
  ): () => void;
  emit(event: E): void;
  clear(): void;
}

function createEmitter<E extends { type: string }>(): Emitter<E> {
  const handlers = new Map<string, Set<(payload: E) => void>>();
  return {
    on(event, handler) {
      let set = handlers.get(event);
      if (!set) {
        set = new Set();
        handlers.set(event, set);
      }
      const wrapped = handler as (payload: E) => void;
      set.add(wrapped);
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        handlers.get(event)?.delete(wrapped);
      };
    },
    emit(event) {
      const set = handlers.get(event.type);
      if (!set) return;
      for (const h of Array.from(set)) h(event);
    },
    clear() {
      handlers.clear();
    }
  };
}
