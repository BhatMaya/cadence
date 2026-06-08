import { CadenceApiError, normalizeApiBaseUrl } from './api.js';
import { createCapture } from './capture.js';
export class CadenceAuthClient {
    constructor(options) {
        this.apiBaseUrl = normalizeApiBaseUrl(options.apiBaseUrl);
        this.apiKey = options.apiKey;
        this.fetchImpl = options.fetchImpl ?? fetch;
        this.detectMobile = options.detectMobile;
    }
    signup(request) {
        requireText('CadenceAuthClient.signup', 'email', request.email);
        requireText('CadenceAuthClient.signup', 'username', request.username);
        requireText('CadenceAuthClient.signup', 'password', request.password);
        return this.post('/signup', request);
    }
    authenticate(request) {
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
    verifyCode(request) {
        requireText('CadenceAuthClient.verifyCode', 'login_attempt_id', request.login_attempt_id);
        requireText('CadenceAuthClient.verifyCode', 'code', request.code);
        return this.post('/code_verification', request);
    }
    resendCode(request) {
        requireText('CadenceAuthClient.resendCode', 'login_attempt_id', request.login_attempt_id);
        return this.post('/resend_code', request);
    }
    logout(request) {
        requireText('CadenceAuthClient.logout', 'username', request.username);
        return this.post('/logout', request);
    }
    async post(path, body) {
        const headers = { 'Content-Type': 'application/json' };
        if (this.apiKey)
            headers.Authorization = `Bearer ${this.apiKey}`;
        const response = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(body)
        });
        const responseBody = await parseResponseBody(response);
        // Authentication endpoints use JSON status payloads for user-facing
        // failures such as invalid credentials. Return those bodies so callers
        // can render the backend message without exception control flow.
        if (!response.ok && !isJsonObject(responseBody)) {
            throw new CadenceApiError(`Cadence auth request failed with status ${response.status}`, response.status, responseBody);
        }
        return responseBody;
    }
}
export function createCadenceAuthClient(options) {
    return new CadenceAuthClient(options);
}
export function createPasswordAuthController(options) {
    const client = new CadenceAuthClient(options);
    const mode = options.mode ?? 'password';
    const clearPasswordOnCaptureRejection = options.clearPasswordOnCaptureRejection ?? true;
    const restartOnRetryableResult = options.restartOnRetryableResult ?? true;
    const capture = createControllerCapture(options, mode);
    const restart = () => {
        capture.start();
    };
    const currentUsername = () => {
        if (typeof options.username === 'function')
            return options.username();
        if (typeof options.username === 'string')
            return options.username;
        return options.usernameInput?.value ?? '';
    };
    const retryIfNeeded = (result) => {
        if (!restartOnRetryableResult)
            return;
        if (result.status === 'capture_rejected' ||
            result.status === 'capture_error' ||
            result.status === 'error' ||
            result.status === 'user not found') {
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
        async signIn(signInOptions = {}) {
            const username = signInOptions.username ?? currentUsername();
            const password = signInOptions.password ?? options.passwordInput.value;
            requireText('PasswordAuthController.signIn', 'username', username);
            requireText('PasswordAuthController.signIn', 'password', password);
            const captureEvent = await stopForCaptureEvent(capture);
            if (captureEvent.type === 'sample_rejected') {
                if (clearPasswordOnCaptureRejection)
                    options.passwordInput.value = '';
                const result = {
                    status: 'capture_rejected',
                    reason: captureEvent.reason,
                    message: captureRejectionMessage(captureEvent.reason)
                };
                retryIfNeeded(result);
                return result;
            }
            if (captureEvent.type === 'error') {
                const result = {
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
        verifyCode(request) {
            return client.verifyCode(request);
        },
        resendCode(request) {
            return client.resendCode(request);
        },
        logout(request) {
            return client.logout(request ?? { username: currentUsername() });
        },
        on(event, handler) {
            return capture.on(event, handler);
        }
    };
}
function createControllerCapture(options, mode) {
    return createCapture({
        target: options.passwordInput,
        mode,
        fieldId: options.fieldId,
        minLength: options.minLength ?? 8,
        maxInterKeyPauseMs: options.maxInterKeyPauseMs
    });
}
function stopForCaptureEvent(capture) {
    return new Promise((resolve) => {
        let unsubscribeReady = () => { };
        let unsubscribeRejected = () => { };
        let unsubscribeError = () => { };
        const finish = (event) => {
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
function captureRejectionMessage(reason) {
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
function detectMobileDevice() {
    if (typeof window === 'undefined' || typeof navigator === 'undefined')
        return false;
    return (navigator.maxTouchPoints > 0 &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(pointer: coarse)').matches);
}
async function parseResponseBody(response) {
    const text = await response.text();
    if (!text)
        return null;
    try {
        return JSON.parse(text);
    }
    catch {
        return text;
    }
}
function isJsonObject(value) {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function requireText(scope, field, value) {
    if (!value || !value.trim()) {
        throw new TypeError(`${scope}: ${field} is required`);
    }
}
//# sourceMappingURL=auth.js.map