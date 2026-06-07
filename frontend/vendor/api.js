export class CadenceApiError extends Error {
    constructor(message, status, body) {
        super(message);
        this.name = 'CadenceApiError';
        this.status = status;
        this.body = body;
    }
}
export class CadenceClient {
    constructor(options) {
        if (!options.apiBaseUrl) {
            throw new TypeError('CadenceClient: apiBaseUrl is required');
        }
        if (!options.apiKey) {
            throw new TypeError('CadenceClient: apiKey is required');
        }
        this.apiBaseUrl = options.apiBaseUrl.replace(/\/+$/, '');
        this.apiKey = options.apiKey;
        this.fetchImpl = options.fetchImpl ?? fetch;
    }
    async request(path, init = {}) {
        const response = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
            method: init.method ?? 'GET',
            headers: {
                Authorization: `Bearer ${this.apiKey}`,
                'Content-Type': 'application/json'
            },
            body: init.body === undefined ? undefined : JSON.stringify(init.body)
        });
        const body = await parseResponseBody(response);
        if (!response.ok) {
            throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
        }
        return body;
    }
}
export function createCadenceClient(options) {
    return new CadenceClient(options);
}
export async function submitAppRegistration(options, request) {
    if (!options.apiBaseUrl) {
        throw new TypeError('submitAppRegistration: apiBaseUrl is required');
    }
    const fetchImpl = options.fetchImpl ?? fetch;
    const response = await fetchImpl(`${options.apiBaseUrl.replace(/\/+$/, '')}/v1/app-registrations`, {
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
    if (!appRegistrationId) {
        throw new TypeError('getAppRegistrationStatus: appRegistrationId is required');
    }
    const fetchImpl = options.fetchImpl ?? fetch;
    const headers = { 'Content-Type': 'application/json' };
    if (options.lookupToken) {
        headers.Authorization = `Bearer ${options.lookupToken}`;
    }
    const response = await fetchImpl(`${options.apiBaseUrl.replace(/\/+$/, '')}/v1/app-registrations/${encodeURIComponent(appRegistrationId)}/status`, {
        method: 'GET',
        headers
    });
    const body = await parseResponseBody(response);
    if (!response.ok) {
        throw new CadenceApiError(errorMessage(body, response.status), response.status, body);
    }
    return body;
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
