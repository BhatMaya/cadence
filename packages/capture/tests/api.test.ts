import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  CadenceApiError,
  createCadenceAuthClient,
  createPasswordAuthController,
  getAppRegistrationStatus,
  submitAppRegistration
} from '../src/index.js';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

function trustedKey(target: HTMLElement, type: 'keydown' | 'keyup', code: string, timeStamp: number) {
  const ev = new KeyboardEvent(type, { code });
  Object.defineProperty(ev, 'timeStamp', { value: timeStamp, configurable: true });
  Object.defineProperty(ev, '__cadenceTestTrusted', { value: true, configurable: true });
  target.dispatchEvent(ev);
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('app registration helpers', () => {
  it('submits app registration requests without a bearer token', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        status: 'submitted',
        registration: {
          app_registration_id: 'registration-1',
          name: 'Partner App',
          slug: 'partner-app',
          contact_email: 'dev@partner.example',
          allowed_origins: ['https://partner.example'],
          status: 'pending'
        },
        lookup_token: 'reg_status_test'
      }, 201)
    );

    const result = await submitAppRegistration(
      {
        apiBaseUrl: 'https://api.example.test/',
        fetchImpl
      },
      {
        name: 'Partner App',
        contact_email: 'dev@partner.example',
        allowed_origins: ['https://partner.example']
      }
    );

    expect(result.registration.status).toBe('pending');
    expect(result.lookup_token).toBe('reg_status_test');
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://api.example.test/v1/app-registrations',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: 'Partner App',
          contact_email: 'dev@partner.example',
          allowed_origins: ['https://partner.example']
        })
      })
    );
  });

  it('fetches app registration status with a lookup token', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        status: 'ok',
        registration: {
          app_registration_id: 'registration-1',
          name: 'Partner App',
          slug: 'partner-app',
          contact_email: 'dev@partner.example',
          allowed_origins: ['https://partner.example'],
          status: 'approved',
          application_id: 'app-1'
        }
      })
    );

    const result = await getAppRegistrationStatus(
      {
        apiBaseUrl: 'https://api.example.test/',
        lookupToken: 'reg_status_test',
        fetchImpl
      },
      'registration/1'
    );

    expect(result.registration.status).toBe('approved');
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://api.example.test/v1/app-registrations/registration%2F1/status',
      expect.objectContaining({
        method: 'GET',
        headers: {
          Authorization: 'Bearer reg_status_test',
          'Content-Type': 'application/json'
        }
      })
    );
  });
});

describe('CadenceAuthClient', () => {
  it('calls the deployed signup route', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ status: 'signup_success', user_id: 'user-1' })
    );
    const client = createCadenceAuthClient({
      apiBaseUrl: 'https://api.example.test/',
      apiKey: 'sk_live_test',
      fetchImpl
    });

    const result = await client.signup({
      email: 'dev@example.test',
      username: 'alice',
      password: 'correct horse'
    });

    expect(result.user_id).toBe('user-1');
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://api.example.test/signup',
      expect.objectContaining({
        method: 'POST',
        headers: {
          Authorization: 'Bearer sk_live_test',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          email: 'dev@example.test',
          username: 'alice',
          password: 'correct horse'
        })
      })
    );
  });

  it('authenticates with raw_data and mobile detection', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        status: '2fa required',
        login_attempt_id: 'attempt-1',
        reason: 'mobile_device',
        enrolled: true,
        enrollment_count: 5,
        enrollment_required: 5,
        enrollment_samples_needed: 0
      })
    );
    const client = createCadenceAuthClient({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl,
      detectMobile: () => true
    });
    const rawData = { events: [{ type: 'down', code: 'KeyA', t: 0 }] as const };

    const result = await client.authenticate({
      username: 'alice',
      password: 'correct horse',
      raw_data: rawData
    });

    expect(result.status).toBe('2fa required');
    expect(fetchImpl).toHaveBeenCalledWith(
      'https://api.example.test/authenticate',
      expect.objectContaining({
        body: JSON.stringify({
          username: 'alice',
          password: 'correct horse',
          raw_data: rawData,
          is_mobile: true
        })
      })
    );
  });

  it('returns auth JSON bodies for expected HTTP failures', async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ status: 'error', message: 'invalid credentials' }, 401)
    );
    const client = createCadenceAuthClient({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl
    });

    const result = await client.authenticate({
      username: 'alice',
      password: 'wrong',
      raw_data: { events: [] }
    });

    expect(result).toEqual({ status: 'error', message: 'invalid credentials' });
  });

  it('throws CadenceApiError when a failed response has no JSON body', async () => {
    const fetchImpl = vi.fn(async () => new Response('upstream unavailable', { status: 502 }));
    const client = createCadenceAuthClient({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl
    });

    await expect(
      client.logout({ username: 'alice' })
    ).rejects.toBeInstanceOf(CadenceApiError);
  });

  it('verifies, resends, and logs out through the current auth routes', async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith('/code_verification')) return jsonResponse({ status: 'accepted' });
      if (url.endsWith('/resend_code')) return jsonResponse({ status: 'code sent' });
      return jsonResponse({ status: 'logged out' });
    });
    const client = createCadenceAuthClient({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl
    });

    await expect(client.verifyCode({ login_attempt_id: 'attempt-1', code: '123456' }))
      .resolves.toMatchObject({ status: 'accepted' });
    await expect(client.resendCode({ login_attempt_id: 'attempt-1' }))
      .resolves.toMatchObject({ status: 'code sent' });
    await expect(client.logout({ username: 'alice' }))
      .resolves.toMatchObject({ status: 'logged out' });
  });
});

describe('createPasswordAuthController', () => {
  it('captures password typing and submits authenticate with minimal app code', async () => {
    const usernameInput = document.createElement('input');
    usernameInput.value = 'alice';
    const passwordInput = document.createElement('input');
    passwordInput.id = 'password';
    passwordInput.value = 'correct horse';
    document.body.append(usernameInput, passwordInput);

    const fetchImpl = vi.fn(async () =>
      jsonResponse({
        status: 'accepted',
        enrolled: true,
        enrollment_count: 5,
        enrollment_required: 5,
        enrollment_samples_needed: 0
      })
    );
    const controller = createPasswordAuthController({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl,
      usernameInput,
      passwordInput,
      minLength: 1,
      detectMobile: () => false
    });

    controller.start();
    trustedKey(passwordInput, 'keydown', 'KeyA', 10);
    trustedKey(passwordInput, 'keyup', 'KeyA', 90);

    const result = await controller.signIn();

    expect(result.status).toBe('accepted');
    const request = JSON.parse(String(fetchImpl.mock.calls[0]?.[1]?.body));
    expect(fetchImpl.mock.calls[0]?.[0]).toBe('https://api.example.test/authenticate');
    expect(request.username).toBe('alice');
    expect(request.password).toBe('correct horse');
    expect(request.is_mobile).toBe(false);
    expect(request.raw_data.events).toHaveLength(2);
    expect(request.raw_data.events[0]).toMatchObject({ type: 'down', code: 'KeyA' });
    expect(request.raw_data.events[1]).toMatchObject({ type: 'up', code: 'KeyA' });
    expect(request.raw_data.events[1].t - request.raw_data.events[0].t).toBe(80);
  });

  it('returns capture_rejected and clears the password before retry', async () => {
    const usernameInput = document.createElement('input');
    usernameInput.value = 'alice';
    const passwordInput = document.createElement('input');
    passwordInput.value = 'short';
    document.body.append(usernameInput, passwordInput);

    const fetchImpl = vi.fn();
    const controller = createPasswordAuthController({
      apiBaseUrl: 'https://api.example.test',
      fetchImpl,
      usernameInput,
      passwordInput,
      minLength: 3
    });

    controller.start();
    trustedKey(passwordInput, 'keydown', 'KeyA', 10);
    trustedKey(passwordInput, 'keyup', 'KeyA', 90);

    const result = await controller.signIn();

    expect(result).toMatchObject({
      status: 'capture_rejected',
      reason: 'below_min_length'
    });
    expect(passwordInput.value).toBe('');
    expect(fetchImpl).not.toHaveBeenCalled();

    controller.destroy();
  });
});
