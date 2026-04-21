export type CaptureMode = 'password' | 'username' | 'freetext';

export type RejectionReason =
  | 'below_min_length'
  | 'poisoned'
  | 'timing_resolution_inadequate'
  | 'empty_sample'
  | 'session_not_started';

/**
 * A single key event as it appears inside a Sample. Timestamps are
 * session-relative (ms from Sample.session_start_ms).
 */
export interface SampleKeyEvent {
  readonly type: 'down' | 'up';
  readonly code: string;
  readonly t: number;
}

export interface SampleEnv {
  readonly user_agent: string;
  readonly timing_resolution_ms: number;
}

/**
 * Serialized output of a capture session. Sent to the backend and
 * consumed by the ML pipeline.
 */
export interface Sample {
  readonly session_id: string;
  readonly field_id: string;
  /** Wall-clock (`Date.now()`) at session start. Correlation only, not features. */
  readonly session_start_ms: number;
  readonly events: readonly SampleKeyEvent[];
  /** Contamination flag names raised during the session (set semantics). */
  readonly flags: readonly string[];
  /** Count of events dropped because `event.isTrusted === false`. */
  readonly untrusted_events: number;
  readonly env: SampleEnv;
  /** 0–1, higher is better. Scoring formula lands in Phase 4; stub emits 1.0. */
  readonly quality_score: number;
  readonly library_version: string;
}

/**
 * Options for {@link createCapture}.
 */
export interface CaptureOptions {
  /** Input element to attach keystroke listeners to. */
  target: HTMLElement;
  /** Security/strictness profile. See {@link CaptureMode}. */
  mode: CaptureMode;
  /** Logical field identifier. Defaults to `target.id`, then `name` attr, then `""`. */
  fieldId?: string;
  /** Convenience callback; also available via `on('sample_ready', ...)`. */
  onSample?: (sample: Sample) => void;
  /** Minimum keydown count for a sample to be accepted. */
  minLength?: number;
  /** Inter-key pause in ms that triggers a contamination flag. Default 5000. */
  maxInterKeyPauseMs?: number;
}

export type CaptureEvent =
  | { type: 'sample_ready'; sample: Sample }
  | { type: 'sample_rejected'; reason: RejectionReason }
  | { type: 'error'; error: Error };

export interface Capture {
  start(): void;
  stop(): void;
  destroy(): void;
  on<E extends CaptureEvent['type']>(
    event: E,
    handler: (payload: Extract<CaptureEvent, { type: E }>) => void
  ): () => void;
}
