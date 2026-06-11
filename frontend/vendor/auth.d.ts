import type { CaptureEvent, CaptureMode, RejectionReason, Sample } from './types.js';
export interface CadenceAuthClientOptions {
    apiBaseUrl: string;
    apiKey?: string;
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
    raw_data: Sample | {
        events: Sample['events'];
    } | {
        keystrokes: readonly unknown[];
    };
    is_mobile?: boolean;
}
export interface AuthenticateResponse extends AuthenticationEnrollmentState {
    readonly status: 'accepted' | '2fa required' | 'pending 2fa' | 'account is locked' | 'password_locked' | 'logged in' | 'user not found' | 'error' | string;
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
export interface ChangePasswordRequest {
    username: string;
    current_password: string;
    new_password: string;
}
export interface ChangePasswordResponse {
    readonly status: 'password_changed' | 'user not found' | 'error' | string;
    readonly user_id?: string;
    readonly message?: string;
}
export interface UnblockUserRequest {
    username?: string;
    user_id?: string;
}
export interface UnblockUserResponse {
    readonly status: 'unblocked' | 'user not found' | 'error' | string;
    readonly user_id?: string;
    readonly current_login_status?: string;
    readonly message?: string;
}
export declare class CadenceAuthClient {
    private readonly apiBaseUrl;
    private readonly apiKey?;
    private readonly fetchImpl;
    private readonly detectMobile?;
    constructor(options: CadenceAuthClientOptions);
    signup(request: SignupRequest): Promise<SignupResponse>;
    authenticate(request: AuthenticateRequest): Promise<AuthenticateResponse>;
    verifyCode(request: VerifyCodeRequest): Promise<VerifyCodeResponse>;
    resendCode(request: ResendCodeRequest): Promise<ResendCodeResponse>;
    logout(request: LogoutRequest): Promise<LogoutResponse>;
    changePassword(request: ChangePasswordRequest): Promise<ChangePasswordResponse>;
    unblockUser(request: UnblockUserRequest): Promise<UnblockUserResponse>;
    private post;
}
export declare function createCadenceAuthClient(options: CadenceAuthClientOptions): CadenceAuthClient;
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
export type PasswordSignInResponse = AuthenticateResponse | CaptureRejectedAuthResponse | CaptureErrorAuthResponse;
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
    changePassword(request: ChangePasswordRequest): Promise<ChangePasswordResponse>;
    unblockUser(request?: UnblockUserRequest): Promise<UnblockUserResponse>;
    on<E extends CaptureEvent['type']>(event: E, handler: (payload: Extract<CaptureEvent, {
        type: E;
    }>) => void): () => void;
}
export declare function createPasswordAuthController(options: PasswordAuthControllerOptions): PasswordAuthController;
