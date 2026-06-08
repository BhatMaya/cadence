import { CadenceApiError, normalizeApiBaseUrl } from './api.js';
import { createCapture } from './capture.js';
import type {
  Capture,
  CaptureEvent,
  CaptureMode,
  RejectionReason,
  Sample
} from './types.js';

export interface CadenceAuthClientOptions {
  apiBaseUrl: string;
  fetchImpl?: typeof fetch;
  detectMobile?: () => boolean;
}

export interface SignupRequest {
  email: string;
  username: string;
  password: string;
}

export interface SignupResponse {
  readonly status: 'signup_success' | 'error' | string;
  readonly user_id?: string;
  readonly message?: string;
}

export interface AuthenticationEnrollmentState {
  readonly enrolled?: boolean;
  readonly enrollment_count?: number;
  readonly enrollment_required?: number;
  readonly enrollment_samples_needed?: number;
}

export interface AuthenticateRequest {
  username: string;
  password: string;
  raw_data: Sample | { events: Sample['events'] } | { keystrokes: readonly unknown[] };
  is_mobile?: boolean;
}

export interface AuthenticateResponse extends AuthenticationEnrollmentState {
  readonly status:
    | 'accepted'
    | '2fa required'
    | 'pending 2fa'
    | 'account is locked'
    | 'password_locked'
    | 'logged in'
    | 'user not found'
    | 'error'
    | string;
  readonly message?: string;
  readonly login_attempt_id?: string;
  readonly reason?: string;
  readonly demo_otp?: string;
}

export interface VerifyCodeRequest {
  login_attempt_id: string;
  code: string;
}

export interface VerifyCodeResponse extends AuthenticationEnrollmentState {
  readonly status: 'accepted' | 'rejected' | 'unlocked' | 'error' | string;
  readonly message?: string;
}

export interface ResendCodeRequest {
  login_attempt_id: string;
}

export interface ResendCodeResponse {
  readonly status: 'code sent' | 'invalid attempt' | 'error' | string;
  readonly message?: string;
  readonly demo_otp?: string;
}

export interface LogoutRequest {
  username: string;
}

export interface LogoutResponse {
  readonly status: 'logged out' | 'user not found' | 'error' | string;
  readonly message?: string;
}

export class CadenceAuthClient {
  private readonly apiBaseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly detectMobile?: () => boolean;

  constructor(options: CadenceAuthClientOptions) {
    this.apiBaseUrl = normalizeApiBaseUrl(options.apiBaseUrl);
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.detectMobile = options.detectMobile;
  }

  signup(request: SignupRequest): Promise<SignupResponse> {
    requireText('CadenceAuthClient.signup', 'email', request.email);
    requireText('CadenceAuthClient.signup', 'username', request.username);
    requireText('CadenceAuthClient.signup', 'password', request.password);
    return this.post('/signup', request);
  }

  authenticate(request: AuthenticateRequest): Promise<AuthenticateResponse> {
    requireText('CadenceAuthClient.authenticate', 'username', request.username);
    requireText('CadenceAuthClient.authenticate', 'password', request.password);
    if (!request.raw_data) {
      throw new TypeError('CadenceAuthClient.authenticate: raw_data is required');
    }
    return this.post('/authenticate', {
      ...request,
      is_mobile: request.is_mobile ?? this.detectMobile?.() ?? detectMobileDevice()
    });
  }

  verifyCode(request: VerifyCodeRequest): Promise<VerifyCodeResponse> {
    requireText('CadenceAuthClient.verifyCode', 'login_attempt_id', request.login_attempt_id);
    requireText('CadenceAuthClient.verifyCode', 'code', request.code);
    return this.post('/code_verification', request);
  }

  resendCode(request: ResendCodeRequest): Promise<ResendCodeResponse> {
    requireText('CadenceAuthClient.resendCode', 'login_attempt_id', request.login_attempt_id);
    return this.post('/resend_code', request);
  }

  logout(request: LogoutRequest): Promise<LogoutResponse> {
    requireText('CadenceAuthClient.logout', 'username', request.username);
    return this.post('/logout', request);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    const response = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const responseBody = await parseResponseBody(response);

    // Authentication endpoints use JSON status payloads for user-facing
    // failures such as invalid credentials. Return those bodies so callers
    // can render the backend message without exception control flow.
    if (!response.ok && !isJsonObject(responseBody)) {
      throw new CadenceApiError(
        `Cadence auth request failed with status ${response.status}`,
        response.status,
        responseBody
      );
    }
    return responseBody as T;
  }
}

export function createCadenceAuthClient(options: CadenceAuthClientOptions): CadenceAuthClient {
  return new CadenceAuthClient(options);
}

export interface PasswordAuthControllerOptions extends CadenceAuthClientOptions {
  passwordInput: HTMLInputElement | HTMLTextAreaElement;
  usernameInput?: HTMLInputElement;
  username?: string | (() => string);
  fieldId?: string;
  minLength?: number;
  maxInterKeyPauseMs?: number;
  mode?: Extract<CaptureMode, 'password' | 'username'>;
  clearPasswordOnCaptureRejection?: boolean;
  restartOnRetryableResult?: boolean;
}

export interface SignInOptions {
  username?: string;
  password?: string;
  is_mobile?: boolean;
}

export interface CaptureRejectedAuthResponse {
  readonly status: 'capture_rejected';
  readonly reason: RejectionReason;
  readonly message: string;
}

export interface CaptureErrorAuthResponse {
  readonly status: 'capture_error';
  readonly message: string;
  readonly error: Error;
}

export type PasswordSignInResponse =
  | AuthenticateResponse
  | CaptureRejectedAuthResponse
  | CaptureErrorAuthResponse;

export interface PasswordAuthController {
  readonly client: CadenceAuthClient;
  start(): void;
  stop(): Promise<CaptureEvent>;
  restart(): void;
  destroy(): void;
  signIn(options?: SignInOptions): Promise<PasswordSignInResponse>;
  verifyCode(request: VerifyCodeRequest): Promise<VerifyCodeResponse>;
  resendCode(request: ResendCodeRequest): Promise<ResendCodeResponse>;
  logout(request?: LogoutRequest): Promise<LogoutResponse>;
  on<E extends CaptureEvent['type']>(
    event: E,
    handler: (payload: Extract<CaptureEvent, { type: E }>) => void
  ): () => void;
}

export function createPasswordAuthController(
  options: PasswordAuthControllerOptions
): PasswordAuthController {
  const client = new CadenceAuthClient(options);
  const mode = options.mode ?? 'password';
  const clearPasswordOnCaptureRejection = options.clearPasswordOnCaptureRejection ?? true;
  const restartOnRetryableResult = options.restartOnRetryableResult ?? true;
  const capture = createControllerCapture(options, mode);

  const restart = (): void => {
    capture.start();
  };

  const currentUsername = (): string => {
    if (typeof options.username === 'function') return options.username();
    if (typeof options.username === 'string') return options.username;
    return options.usernameInput?.value ?? '';
  };

  const retryIfNeeded = (result: PasswordSignInResponse): void => {
    if (!restartOnRetryableResult) return;
    if (
      result.status === 'capture_rejected' ||
      result.status === 'capture_error' ||
      result.status === 'error' ||
      result.status === 'user not found'
    ) {
      restart();
    }
  };

  return {
    client,

    start() {
      capture.start();
    },

    stop() {
      return stopForCaptureEvent(capture);
    },

    restart,

    destroy() {
      capture.destroy();
    },

    async signIn(signInOptions: SignInOptions = {}) {
      const username = signInOptions.username ?? currentUsername();
      const password = signInOptions.password ?? options.passwordInput.value;
      requireText('PasswordAuthController.signIn', 'username', username);
      requireText('PasswordAuthController.signIn', 'password', password);

      const captureEvent = await stopForCaptureEvent(capture);
      if (captureEvent.type === 'sample_rejected') {
        if (clearPasswordOnCaptureRejection) options.passwordInput.value = '';
        const result: CaptureRejectedAuthResponse = {
          status: 'capture_rejected',
          reason: captureEvent.reason,
          message: captureRejectionMessage(captureEvent.reason)
        };
        retryIfNeeded(result);
        return result;
      }
      if (captureEvent.type === 'error') {
        const result: CaptureErrorAuthResponse = {
          status: 'capture_error',
          message: captureEvent.error.message,
          error: captureEvent.error
        };
        retryIfNeeded(result);
        return result;
      }

      const result = await client.authenticate({
        username,
        password,
        raw_data: captureEvent.sample,
        is_mobile: signInOptions.is_mobile
      });
      retryIfNeeded(result);
      return result;
    },

    verifyCode(request: VerifyCodeRequest) {
      return client.verifyCode(request);
    },

    resendCode(request: ResendCodeRequest) {
      return client.resendCode(request);
    },

    logout(request?: LogoutRequest) {
      return client.logout(request ?? { username: currentUsername() });
    },

    on(event, handler) {
      return capture.on(event, handler);
    }
  };
}

function createControllerCapture(
  options: PasswordAuthControllerOptions,
  mode: Extract<CaptureMode, 'password' | 'username'>
): Capture {
  return createCapture({
    target: options.passwordInput,
    mode,
    fieldId: options.fieldId,
    minLength: options.minLength ?? 8,
    maxInterKeyPauseMs: options.maxInterKeyPauseMs
  });
}

function stopForCaptureEvent(capture: Capture): Promise<CaptureEvent> {
  return new Promise((resolve) => {
    let unsubscribeReady = (): void => {};
    let unsubscribeRejected = (): void => {};
    let unsubscribeError = (): void => {};
    const finish = (event: CaptureEvent): void => {
      unsubscribeReady();
      unsubscribeRejected();
      unsubscribeError();
      resolve(event);
    };
    unsubscribeReady = capture.on('sample_ready', finish);
    unsubscribeRejected = capture.on('sample_rejected', finish);
    unsubscribeError = capture.on('error', finish);
    capture.stop();
  });
}

function captureRejectionMessage(reason: RejectionReason): string {
  switch (reason) {
    case 'below_min_length':
      return 'Type the password manually before signing in.';
    case 'poisoned':
      return 'Clear the password field and type it manually before signing in.';
    case 'timing_resolution_inadequate':
      return 'This browser cannot capture precise enough timing data.';
    case 'empty_sample':
      return 'Type the password manually before signing in.';
    case 'session_not_started':
      return 'Password capture has not started yet.';
  }
}

function detectMobileDevice(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false;
  return (
    navigator.maxTouchPoints > 0 &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(pointer: coarse)').matches
  );
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireText(scope: string, field: string, value: string): void {
  if (!value || !value.trim()) {
    throw new TypeError(`${scope}: ${field} is required`);
  }
}
