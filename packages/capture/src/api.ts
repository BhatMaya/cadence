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

export interface EnrollmentState {
  readonly enrolled: boolean;
  readonly enrollment_count: number;
  readonly enrollment_required: number;
  readonly enrollment_samples_needed: number;
}

export class CadenceApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'CadenceApiError';
    this.status = status;
    this.body = body;
  }
}

export async function submitAppRegistration(
  options: SubmitAppRegistrationOptions,
  request: AppRegistrationRequest
): Promise<AppRegistrationResponse> {
  if (!options.apiBaseUrl) {
    throw new TypeError('submitAppRegistration: apiBaseUrl is required');
  }
  const fetchImpl = options.fetchImpl ?? fetch;
  const response = await fetchImpl(
    `${normalizeApiBaseUrl(options.apiBaseUrl)}/v1/app-registrations`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request)
    }
  );
  const body = await parseResponseBody(response);
  if (!response.ok) {
    throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
  }
  return body as AppRegistrationResponse;
}

export async function getAppRegistrationStatus(
  options: AppRegistrationStatusOptions,
  appRegistrationId: string
): Promise<AppRegistrationStatusResponse> {
  if (!options.apiBaseUrl) {
    throw new TypeError('getAppRegistrationStatus: apiBaseUrl is required');
  }
  if (!options.lookupToken) {
    throw new TypeError('getAppRegistrationStatus: lookupToken is required');
  }
  if (!appRegistrationId) {
    throw new TypeError('getAppRegistrationStatus: appRegistrationId is required');
  }
  const fetchImpl = options.fetchImpl ?? fetch;
  const response = await fetchImpl(
    `${normalizeApiBaseUrl(options.apiBaseUrl)}/v1/app-registrations/${encodeURIComponent(appRegistrationId)}/status`,
    {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${options.lookupToken}`,
        'Content-Type': 'application/json'
      }
    }
  );
  const body = await parseResponseBody(response);
  if (!response.ok) {
    throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
  }
  return body as AppRegistrationStatusResponse;
}

export function normalizeApiBaseUrl(apiBaseUrl: string): string {
  if (!apiBaseUrl) {
    throw new TypeError('apiBaseUrl is required');
  }
  return apiBaseUrl.replace(/\/+$/, '');
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

function errorMessage(body: unknown, status: number): string {
  if (
    typeof body === 'object' &&
    body !== null &&
    'message' in body &&
    typeof (body as { message?: unknown }).message === 'string'
  ) {
    return (body as { message: string }).message;
  }
  return `Cadence API request failed with status ${status}`;
}
