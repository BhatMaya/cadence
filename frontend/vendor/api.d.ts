export interface SubmitAppRegistrationOptions {
    apiBaseUrl: string;
    fetchImpl?: typeof fetch;
}
export interface AppRegistrationStatusOptions {
    apiBaseUrl: string;
    lookupToken: string;
    fetchImpl?: typeof fetch;
}
export interface AppRegistrationRequest {
    name: string;
    contact_email: string;
    slug?: string;
    allowed_origins?: readonly string[];
    use_case?: string;
}
export interface AppRegistration {
    readonly app_registration_id: string;
    readonly name: string;
    readonly slug: string;
    readonly contact_email: string;
    readonly allowed_origins: readonly string[];
    readonly use_case?: string | null;
    readonly status: 'pending' | 'approved' | 'rejected';
    readonly application_id?: string | null;
    readonly reviewed_at?: string | null;
    readonly created_at?: string;
    readonly updated_at?: string;
}
export interface AppRegistrationResponse {
    readonly status: 'submitted';
    readonly registration: AppRegistration;
    readonly lookup_token: string;
}
export interface AppRegistrationStatusResponse {
    readonly status: 'ok';
    readonly registration: AppRegistration;
}
export declare class CadenceApiError extends Error {
    readonly status: number;
    readonly body: unknown;
    constructor(message: string, status: number, body: unknown);
}
export declare function submitAppRegistration(options: SubmitAppRegistrationOptions, request: AppRegistrationRequest): Promise<AppRegistrationResponse>;
export declare function getAppRegistrationStatus(options: AppRegistrationStatusOptions, appRegistrationId: string): Promise<AppRegistrationStatusResponse>;
export declare function normalizeApiBaseUrl(apiBaseUrl: string): string;
