# Cadence npm package

TypeScript utilities for integrating an application with Cadence password
auth, typing capture, and step-up 2FA.

## Install

```bash
npm install @cadence-auth/cadence
```

## Minimal password auth integration

```ts
import { createPasswordAuthController } from '@cadence-auth/cadence';

const auth = createPasswordAuthController({
  apiBaseUrl: '/api/cadence',
  usernameInput: document.querySelector<HTMLInputElement>('#username')!,
  passwordInput: document.querySelector<HTMLInputElement>('#password')!,
  minLength: 8
});

document.querySelector('form')!.addEventListener('submit', async (event) => {
  event.preventDefault();
  const result = await auth.signIn();

  if (result.status === 'accepted' || result.status === 'logged in') {
    window.location.assign('/dashboard');
    return;
  }

  if (result.status === '2fa required' || result.status === 'password_locked') {
    showCodeForm(result.login_attempt_id!);
    return;
  }

  showError(result.message ?? result.status);
});

document.querySelector('#password')!.addEventListener('focus', () => auth.start());
```

The controller owns the capture lifecycle. It starts keystroke capture, stops it
on submit, sends `/authenticate`, clears poisoned samples, restarts capture for
retryable failures, and exposes helpers for `/code_verification`,
`/resend_code`, `/logout`, `/password/change`, and `/users/unblock`.

## App-scoped auth client

```ts
import { createCadenceAuthClient } from '@cadence-auth/cadence';

const cadence = createCadenceAuthClient({
  apiBaseUrl: process.env.CADENCE_API_BASE!,
  apiKey: process.env.CADENCE_API_KEY!
});

await cadence.signup({
  email: 'user@example.com',
  username: 'alice',
  password: 'correct horse battery staple'
});

const login = await cadence.authenticate({
  username: 'alice',
  password: 'correct horse battery staple',
  raw_data: sampleFromCreateCapture
});

if (login.status === '2fa required') {
  await cadence.verifyCode({
    login_attempt_id: login.login_attempt_id!,
    code: '123456'
  });
}

await cadence.changePassword({
  username: 'alice',
  current_password: 'correct horse battery staple',
  new_password: 'a much better password!2'
});

await cadence.unblockUser({ username: 'alice' });
```

Use `apiKey` only from trusted server-side code, or call your own backend
proxy from the browser so `CADENCE_API_KEY` is never exposed.

## Low-level capture

Use this when you want full control over the form flow.

```ts
import { createCadenceAuthClient, createCapture } from '@cadence-auth/cadence';

const cadence = createCadenceAuthClient({
  apiBaseUrl: '/api/cadence'
});
const input = document.querySelector<HTMLInputElement>('#password')!;
const capture = createCapture({
  target: input,
  mode: 'password',
  minLength: 8
});

capture.on('sample_ready', async ({ sample }) => {
  await cadence.authenticate({
    username: 'alice',
    password: input.value,
    raw_data: sample
  });
});

capture.start();
```

## Request app access

```ts
import { getAppRegistrationStatus, submitAppRegistration } from '@cadence-auth/cadence';

const request = await submitAppRegistration(
  { apiBaseUrl: 'https://api.cadence.example' },
  {
    name: 'Acme Dashboard',
    contact_email: 'dev@acme.example',
    allowed_origins: ['https://app.acme.example'],
    use_case: 'Use Cadence password auth and typing-based 2FA'
  }
);

const status = await getAppRegistrationStatus(
  {
    apiBaseUrl: 'https://api.cadence.example',
    lookupToken: request.lookup_token
  },
  request.registration.app_registration_id
);
```

The auth API accepts a Cadence `Sample` with `events` or an object with
precomputed `keystrokes` as `raw_data`.

The repository also includes `docs/release.md` with the publish checklist.
