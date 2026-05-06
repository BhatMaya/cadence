import { createCapture } from './vendor/index.js';

// ---------- config ----------
const API_BASE =
  window.SYNERGYZE_API_BASE ||
  localStorage.getItem('synergyze.api_base') ||
  'http://localhost:5001';

// ---------- routing ----------
const VIEWS = ['landing', 'register', 'login', 'twofa', 'dashboard'];

function showView(name) {
  const target = VIEWS.includes(name) ? name : 'landing';
  for (const view of VIEWS) {
    const el = document.querySelector(`[data-route-view="${view}"]`);
    if (!el) continue;
    el.hidden = view !== target;
  }
  if (target !== 'landing') {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  if (window.location.hash !== `#/${target}`) {
    history.replaceState(null, '', target === 'landing' ? '#' : `#/${target}`);
  }
  onViewChange(target);
}

function readRouteFromHash() {
  const match = window.location.hash.match(/^#\/(\w+)/);
  return match ? match[1] : 'landing';
}

document.addEventListener('click', (ev) => {
  const link = ev.target.closest('[data-route]');
  if (!link) return;
  ev.preventDefault();
  showView(link.dataset.route);
});

window.addEventListener('hashchange', () => showView(readRouteFromHash()));

// ---------- capture session manager ----------
// One active capture session at a time, bound to whichever password
// input is currently visible. Restarts on every view change so a fresh
// sample is collected per attempt.

let activeCapture = null;

function teardownCapture() {
  if (!activeCapture) return;
  try {
    activeCapture.session.destroy();
  } catch {}
  activeCapture = null;
}

function attachCapture(input) {
  teardownCapture();
  if (!input) return;
  let lastSample = null;
  const session = createCapture({
    target: input,
    mode: 'password',
    minLength: 1,
    onSample: (sample) => {
      lastSample = sample;
    }
  });
  session.on('error', (ev) => console.warn('[cadence]', ev.error));
  session.start();
  activeCapture = {
    input,
    session,
    finalize() {
      lastSample = null;
      session.stop();
      return lastSample;
    }
  };
}

function onViewChange(view) {
  if (view === 'login') {
    attachCapture(document.getElementById('login-password'));
  } else if (view === 'register') {
    // Signup doesn't send keystrokes — backend only stores them on
    // /authenticate. Capture is unnecessary here.
    teardownCapture();
  } else {
    teardownCapture();
  }
  if (view !== 'twofa') {
    setDemoOtp(null);
  }
}

function setDemoOtp(otp) {
  const banner = document.getElementById('demo-banner');
  const slot = document.getElementById('demo-otp');
  if (!banner || !slot) return;
  if (otp) {
    slot.textContent = otp;
    banner.hidden = false;
  } else {
    slot.textContent = '000000';
    banner.hidden = true;
  }
}

// ---------- helpers ----------
function setStatus(form, message, kind = '') {
  const node = form.querySelector('[data-form-status]');
  if (!node) return;
  node.textContent = message;
  node.classList.remove('is-error', 'is-success');
  if (kind) node.classList.add(`is-${kind}`);
}

async function api(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  let json;
  try {
    json = await res.json();
  } catch {
    json = { status: 'error', message: `bad response (${res.status})` };
  }
  return { ok: res.ok, status: res.status, json };
}

// ---------- pending login state ----------
// Carried between /authenticate (when 2FA is required) and the
// /code_verification step.
const pendingAuth = {
  username: null,
  loginAttemptId: null
};

// ---------- enrollment UI ----------
function renderEnrollment(payload) {
  const wrap = document.getElementById('enrollment-status');
  const fill = document.getElementById('enrollment-fill');
  const text = document.getElementById('enrollment-text');
  if (!wrap || !fill || !text) return;

  if (
    !payload ||
    typeof payload.enrollment_required !== 'number' ||
    typeof payload.enrollment_count !== 'number'
  ) {
    wrap.hidden = true;
    return;
  }

  const required = payload.enrollment_required;
  const count = Math.min(payload.enrollment_count, required);
  const pct = required > 0 ? (count / required) * 100 : 100;
  fill.style.width = `${pct}%`;
  if (payload.enrolled) {
    text.textContent = `Fully enrolled (${count}/${required}). Biometric login active.`;
  } else {
    const need = payload.enrollment_samples_needed ?? required - count;
    text.textContent = `${count}/${required} clean samples collected — ${need} more to enable biometric login.`;
  }
  wrap.hidden = false;
}

function goToDashboard(message, payload) {
  const sub = document.getElementById('dashboard-sub');
  if (sub && message) sub.textContent = message;
  renderEnrollment(payload);
  showView('dashboard');
}

// ---------- register ----------
const registerForm = document.getElementById('register-form');
registerForm?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const email = document.getElementById('register-email').value.trim();
  const username = document.getElementById('register-username').value.trim();
  const password = document.getElementById('register-password').value;

  if (!email || !username || !password) {
    setStatus(registerForm, 'Please fill in every field.', 'error');
    return;
  }

  setStatus(registerForm, 'Disrupting the auth provider…');
  try {
    const { ok, json } = await api('/signup', { email, username, password });
    if (json.status === 'signup_success') {
      setStatus(
        registerForm,
        'Account created. Redirecting to sign in…',
        'success'
      );
      setTimeout(() => {
        document.getElementById('login-username').value = username;
        showView('login');
      }, 700);
      return;
    }
    setStatus(
      registerForm,
      json.message || `Signup failed (${ok ? 'unknown' : 'server'} error).`,
      'error'
    );
  } catch (err) {
    setStatus(registerForm, `Network error: ${err.message}`, 'error');
  }
});

// ---------- login ----------
const loginForm = document.getElementById('login-form');
loginForm?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;

  if (!username || !password) {
    setStatus(loginForm, 'Please fill in every field.', 'error');
    return;
  }

  // Stop the capture and pull the keystroke sample. The backend's
  // model_service handles `{events: [...]}` directly.
  const sample = activeCapture ? activeCapture.finalize() : null;
  const raw_data = sample ? { events: sample.events } : { events: [] };

  setStatus(loginForm, 'Analyzing your typing rhythm…');
  try {
    const { json } = await api('/authenticate', {
      username,
      password,
      raw_data
    });

    switch (json.status) {
      case 'accepted':
        goToDashboard('Welcome back. The paradigm is yours.', json);
        return;

      case '2fa required':
        pendingAuth.username = username;
        pendingAuth.loginAttemptId = json.login_attempt_id;
        setDemoOtp(json.demo_otp || null);
        const reasonNote =
          json.reason === 'enrollment_required'
            ? `Enrollment in progress (${json.enrollment_count}/${json.enrollment_required}). Check your email for a code.`
            : 'Your typing rhythm looked off. Check your email for a code.';
        setStatus(loginForm, reasonNote, 'success');
        setTimeout(() => showView('twofa'), 600);
        return;

      case 'pending 2fa':
        setStatus(
          loginForm,
          'A previous login is still pending verification.',
          'error'
        );
        return;

      case 'logged in':
        goToDashboard('Already signed in elsewhere. Carry on.', null);
        return;

      case 'account is locked':
        setStatus(
          loginForm,
          'Account locked after too many failed codes. Contact support (us, on Twitter).',
          'error'
        );
        return;

      case 'user not found':
        setStatus(loginForm, 'No account with that username.', 'error');
        return;

      default:
        setStatus(
          loginForm,
          json.message || 'Login failed. Try again.',
          'error'
        );
        // Restart capture so the next attempt records fresh keystrokes.
        attachCapture(document.getElementById('login-password'));
    }
  } catch (err) {
    setStatus(loginForm, `Network error: ${err.message}`, 'error');
    attachCapture(document.getElementById('login-password'));
  }
});

// ---------- 2FA ----------
const twofaForm = document.getElementById('twofa-form');
twofaForm?.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const code = document.getElementById('twofa-code').value.trim();
  if (!pendingAuth.username || !pendingAuth.loginAttemptId) {
    setStatus(twofaForm, 'No pending login. Start over.', 'error');
    return;
  }
  if (!/^\d{6}$/.test(code)) {
    setStatus(twofaForm, 'Enter the 6-digit code.', 'error');
    return;
  }

  setStatus(twofaForm, 'Verifying…');
  try {
    const { json } = await api('/code_verification', {
      username: pendingAuth.username,
      login_attempt_id: pendingAuth.loginAttemptId,
      code
    });

    if (json.status === 'accepted') {
      pendingAuth.username = null;
      pendingAuth.loginAttemptId = null;
      const message = json.enrolled
        ? 'Verified. You are now fully enrolled.'
        : 'Verified. Keep going — every clean login enrolls another sample.';
      goToDashboard(message, json);
      return;
    }
    if (json.message === 'max attempts exceeded') {
      setStatus(
        twofaForm,
        'Max attempts exceeded. Account locked.',
        'error'
      );
      return;
    }
    if (json.message === 'expired') {
      setStatus(twofaForm, 'Code expired. Resend a new one.', 'error');
      return;
    }
    if (json.message === 'invalid attempt') {
      setStatus(twofaForm, 'This login attempt is no longer valid.', 'error');
      return;
    }
    setStatus(twofaForm, 'Wrong code. Try again.', 'error');
  } catch (err) {
    setStatus(twofaForm, `Network error: ${err.message}`, 'error');
  }
});

document.getElementById('twofa-resend')?.addEventListener('click', async (ev) => {
  ev.preventDefault();
  if (!pendingAuth.username || !pendingAuth.loginAttemptId) {
    setStatus(twofaForm, 'No pending login.', 'error');
    return;
  }
  setStatus(twofaForm, 'Sending a new code…');
  try {
    const { json } = await api('/resend_code', {
      username: pendingAuth.username,
      login_attempt_id: pendingAuth.loginAttemptId
    });
    if (json.status === 'code sent') {
      setDemoOtp(json.demo_otp || null);
      setStatus(twofaForm, 'New code sent.', 'success');
    } else {
      setStatus(twofaForm, json.message || 'Could not resend.', 'error');
    }
  } catch (err) {
    setStatus(twofaForm, `Network error: ${err.message}`, 'error');
  }
});

// ---------- logout ----------
document.getElementById('logout-btn')?.addEventListener('click', () => {
  // Backend has no logout endpoint yet; this is a UI-only reset that
  // returns the user to the landing page.
  pendingAuth.username = null;
  pendingAuth.loginAttemptId = null;
  showView('landing');
});

// ---------- boot ----------
showView(readRouteFromHash());
