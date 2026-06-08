export class CadenceApiError extends Error {
    constructor(message, status, body) {
        super(message);
        this.name = 'CadenceApiError';
        this.status = status;
        this.body = body;
    }
}
export async function submitAppRegistration(options, request) {
    if (!options.apiBaseUrl) {
        throw new TypeError('submitAppRegistration: apiBaseUrl is required');
    }
    const fetchImpl = options.fetchImpl ?? fetch;
    const response = await fetchImpl(`${normalizeApiBaseUrl(options.apiBaseUrl)}/v1/app-registrations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request)
    });
    const body = await parseResponseBody(response);
    if (!response.ok) {
        throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
    }
    return body;
}
export async function getAppRegistrationStatus(options, appRegistrationId) {
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
    const response = await fetchImpl(`${normalizeApiBaseUrl(options.apiBaseUrl)}/v1/app-registrations/${encodeURIComponent(appRegistrationId)}/status`, {
        method: 'GET',
        headers: {
            Authorization: `Bearer ${options.lookupToken}`,
            'Content-Type': 'application/json'
        }
    });
    const body = await parseResponseBody(response);
    if (!response.ok) {
        throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
    }
    return body;
}
export function normalizeApiBaseUrl(apiBaseUrl) {
    if (!apiBaseUrl) {
        throw new TypeError('apiBaseUrl is required');
    }
    return apiBaseUrl.replace(/\/+$/, '');
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
function errorMessage(body, status) {
    if (typeof body === 'object' &&
        body !== null &&
        'message' in body &&
        typeof body.message === 'string') {
        return body.message;
    }
    return `Cadence API request failed with status ${status}`;
}
//# sourceMappingURL=api.js.map