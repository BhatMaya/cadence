'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { createCapture } from '../vendor/index.js';

const VIEWS = ['landing', 'register', 'login', 'twofa', 'dashboard'];
const VIEW_SET = new Set(VIEWS);

function readRouteFromLocation() {
  if (typeof window === 'undefined') return 'landing';
  const hashMatch = window.location.hash.match(/^#\/(\w+)/);
  if (hashMatch) return VIEW_SET.has(hashMatch[1]) ? hashMatch[1] : 'landing';
  const pathRoute = window.location.pathname.replace(/^\/+/, '').split('/')[0];
  return VIEW_SET.has(pathRoute) ? pathRoute : 'landing';
}

function getApiBase() {
  if (typeof window === 'undefined') return 'http://localhost:5001';
  return (
    window.SYNERGYZE_API_BASE ||
    process.env.NEXT_PUBLIC_SYNERGYZE_API_BASE ||
    window.localStorage.getItem('synergyze.api_base') ||
    'http://localhost:5001'
  );
}

// Detect mobile/touch devices so the backend can skip biometric scoring
// (trained on desktop typing) and fall straight through to 2FA instead.
// Combines two independent signals — touch capability and coarse pointer
// (touchscreen) — which together are reliable without reading user-agent strings.
function isMobileDevice() {
  if (typeof window === 'undefined') return false;
  return navigator.maxTouchPoints > 0 &&
         window.matchMedia('(pointer: coarse)').matches;
}

function hasSubtleCrypto() {
  return typeof crypto !== 'undefined' && crypto.subtle;
}

// Convert a PEM string (-----BEGIN PUBLIC KEY----- ... -----END PUBLIC KEY-----)
// into a raw ArrayBuffer suitable for crypto.subtle.importKey.
function pemToBuffer(pem) {
  // Strip literal \n sequences (Vercel env var mangling) as well as real
  // whitespace before decoding - both can corrupt the base64 if left in.
  const b64 = pem
    .replace(/\\n/g, '')
    .replace(/-----[^-]+-----/g, '')
    .replace(/\s+/g, '');
  const binary = atob(b64);
  const buf = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) buf[i] = binary.charCodeAt(i);
  return buf.buffer;
}

// Hybrid RSA-OAEP + AES-256-GCM encryption.
// RSA alone can't encrypt arbitrary-length data, so we generate an ephemeral
// AES key per request, encrypt the events JSON with it, then wrap the AES key
// with the server's RSA public key. Only the server's private key can unwrap it.
async function encryptEvents(events, rsaCryptoKey) {
  const aesKey = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 }, true, ['encrypt']
  );
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    aesKey,
    new TextEncoder().encode(JSON.stringify(events))
  );
  const rawAesKey = await crypto.subtle.exportKey('raw', aesKey);
  const encryptedKey = await crypto.subtle.encrypt(
    { name: 'RSA-OAEP' }, rsaCryptoKey, rawAesKey
  );
  const toB64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
  return {
    encrypted_key: toB64(encryptedKey),
    iv:            toB64(iv),
    ciphertext:    toB64(ciphertext),
  };
}

function Status({ value }) {
  const kind = value?.kind ? ` is-${value.kind}` : '';
  return <p className={`auth-meta${kind}`} data-form-status>{value?.message ?? ''}</p>;
}

function Enrollment({ payload }) {
  if (
    !payload ||
    typeof payload.enrollment_required !== 'number' ||
    typeof payload.enrollment_count !== 'number' ||
    payload.enrolled
  ) {
    return null;
  }

  const required = payload.enrollment_required;
  const count = Math.min(payload.enrollment_count, required);
  const pct = required > 0 ? (count / required) * 100 : 100;
  const text = payload.enrolled
    ? `Fully enrolled (${count}/${required}). Biometric login active.`
    : `${count}/${required} clean samples collected - ${
        payload.enrollment_samples_needed ?? required - count
      } more to enable biometric login.`;

  return (
    <div className="enrollment" id="enrollment-status">
      <p className="aside-eyebrow">Enrollment progress</p>
      <div className="enrollment-bar">
        <div className="enrollment-fill" id="enrollment-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="enrollment-text" id="enrollment-text">{text}</p>
    </div>
  );
}

export default function SynergyzeApp({ initialRoute = 'landing' }) {
  const [view, setView] = useState(VIEW_SET.has(initialRoute) ? initialRoute : 'landing');
  const [statuses, setStatuses] = useState({
    register: { message: '', kind: '' },
    login: { message: '', kind: '' },
    twofa: { message: '', kind: '' }
  });
  const [demoOtp, setDemoOtp] = useState(null);
  const [dashboardSub, setDashboardSub] = useState(
    'Welcome back. The paradigm has been shifted on your behalf.'
  );
  const [enrollment, setEnrollment] = useState(null);
  const [activeUsername, setActiveUsername] = useState(null);

  const registerEmailRef = useRef(null);
  const registerUsernameRef = useRef(null);
  const registerPasswordRef = useRef(null);
  const loginUsernameRef = useRef(null);
  const loginPasswordRef = useRef(null);
  const twofaCodeRef = useRef(null);
  const activeCaptureRef = useRef(null);
  const pendingAuthRef = useRef({ username: null, loginAttemptId: null });
  const rsaPublicKeyRef = useRef(null);


  const setStatus = useCallback((form, message, kind = '') => {
    setStatuses((current) => ({
      ...current,
      [form]: { message, kind }
    }));
  }, []);

  const teardownCapture = useCallback(() => {
    const active = activeCaptureRef.current;
    if (!active) return;
    try {
      active.session.destroy();
    } catch {}
    activeCaptureRef.current = null;
  }, []);

  const attachCapture = useCallback((input) => {
    teardownCapture();
    if (!input) return;

    let lastSample = null;
    let lastRejection = null;
    const session = createCapture({
      target: input,
      mode: 'password',
      minLength: 1,
      onSample: (sample) => {
        lastSample = sample;
      }
    });

    session.on('error', (ev) => console.warn('[cadence]', ev.error));
    session.on('sample_rejected', (ev) => {
      lastRejection = ev.reason;
    });
    session.start();
    activeCaptureRef.current = {
      session,
      finalize() {
        lastSample = null;
        lastRejection = null;
        session.stop();
        return { sample: lastSample, rejection: lastRejection };
      }
    };
  }, [teardownCapture]);

  const showView = useCallback((name, { replace = false } = {}) => {
    const target = VIEW_SET.has(name) ? name : 'landing';
    setView(target);
    if (typeof window === 'undefined') return;

    if (target !== 'landing') {
      window.scrollTo({ top: 0, behavior: 'instant' });
    }

    const path = target === 'landing' ? '/' : `/${target}`;
    if (window.location.pathname !== path || window.location.hash.startsWith('#/')) {
      const method = replace ? 'replaceState' : 'pushState';
      window.history[method](null, '', path);
    }
  }, []);

  const routeTo = useCallback((target) => (ev) => {
    ev.preventDefault();
    showView(target);
  }, [showView]);

  const api = useCallback(async (path, body) => {
    const res = await fetch(`${getApiBase()}${path}`, {
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
  }, []);

  const goToDashboard = useCallback((message, payload, username = null) => {
    if (message) setDashboardSub(message);
    setEnrollment(payload);
    setActiveUsername(username);
    showView('dashboard');
  }, [showView]);


  useEffect(() => {
    if (!hasSubtleCrypto()) return;
    fetch(`${getApiBase()}/public-key`)
      .then(r => r.json())
      .then(({ public_key }) => crypto.subtle.importKey(
        'spki', pemToBuffer(public_key),
        { name: 'RSA-OAEP', hash: 'SHA-256' },
        false, ['encrypt']
      ))
      .then(key => { rsaPublicKeyRef.current = key; })
      .catch((err) => { console.error('RSA key import failed:', err); });
  }, []);


  useEffect(() => {
    const syncRoute = () => showView(readRouteFromLocation(), { replace: true });
    syncRoute();
    window.addEventListener('popstate', syncRoute);
    window.addEventListener('hashchange', syncRoute);
    return () => {
      window.removeEventListener('popstate', syncRoute);
      window.removeEventListener('hashchange', syncRoute);
    };
  }, [showView]);

  useEffect(() => {
    if (view === 'login') {
      attachCapture(loginPasswordRef.current);
    } else {
      teardownCapture();
    }
    if (view === 'twofa') {
      if (twofaCodeRef.current) twofaCodeRef.current.value = '';
    } else {
      setDemoOtp(null);
    }
  }, [attachCapture, teardownCapture, view]);

  useEffect(() => teardownCapture, [teardownCapture]);

  const handleRegister = async (ev) => {
    ev.preventDefault();
    const email = registerEmailRef.current.value.trim();
    const username = registerUsernameRef.current.value.trim();
    const password = registerPasswordRef.current.value;

    if (!email || !username || !password) {
      setStatus('register', 'Please fill in every field.', 'error');
      return;
    }
    setStatus('register', 'Disrupting the auth provider...');
    try {
      const { ok, json } = await api('/signup', { email, username, password });
      if (json.status === 'signup_success') {
        setStatus('register', 'Account created. Redirecting to sign in...', 'success');
        window.setTimeout(() => {
          if (loginUsernameRef.current) loginUsernameRef.current.value = username;
          showView('login');
        }, 700);
        return;
      }
      setStatus(
        'register',
        json.message || `Signup failed (${ok ? 'unknown' : 'server'} error).`,
        'error'
      );
    } catch (err) {
      setStatus('register', `Network error: ${err.message}`, 'error');
    }
  };

  const handleLogin = async (ev) => {
    ev.preventDefault();
    const username = loginUsernameRef.current.value.trim();
    const password = loginPasswordRef.current.value;

    if (!username || !password) {
      setStatus('login', 'Please fill in every field.', 'error');
      return;
    }

    const capture = activeCaptureRef.current
      ? activeCaptureRef.current.finalize()
      : { sample: null, rejection: 'session_not_started' };
    const sample = capture.sample;
    if (!sample || sample.events.length === 0) {
      const message =
        capture.rejection === 'poisoned'
          ? 'Clear the password field and type it manually before signing in.'
          : 'Type your password manually before signing in.';
      if (loginPasswordRef.current) loginPasswordRef.current.value = '';
      setStatus('login', message, 'error');
      attachCapture(loginPasswordRef.current);
      return;
    }
    const events = sample ? sample.events : [];
    // Encrypt keystroke events when WebCrypto is available. Plain HTTP on a
    // LAN/Tailscale IP is not a secure browser context, so local demo mode
    // falls back to raw events; the backend accepts that only in demo mode.
    if (hasSubtleCrypto() && !rsaPublicKeyRef.current) {
      setStatus('login', 'Encryption key not ready — please try again.', 'error');
      return;
    }
    const raw_data = hasSubtleCrypto()
      ? await encryptEvents(events, rsaPublicKeyRef.current)
      : { events };
    const is_mobile = isMobileDevice();

    setStatus('login', 'Analyzing your typing rhythm...');
    try {
      const { json } = await api('/authenticate', { username, password, raw_data, is_mobile });

      switch (json.status) {
        case 'accepted':
          goToDashboard('Welcome back. The paradigm is yours.', json, username);
          return;
        case '2fa required': {
          pendingAuthRef.current = {
            username,
            loginAttemptId: json.login_attempt_id
          };
          setDemoOtp(json.demo_otp || null);
          const reasonNote =
            json.reason === 'enrollment_required'
              ? `Enrollment in progress (${json.enrollment_count}/${json.enrollment_required}). Check your email for a code.`
              : json.reason === 'mobile_device'
              ? 'Mobile sign-in detected — check your email for a verification code.'
              : 'Your typing rhythm looked off. Check your email for a code.';
          setStatus('login', reasonNote, 'success');
          window.setTimeout(() => showView('twofa'), 600);
          return;
        }
        case 'pending 2fa':
          setStatus('login', 'A previous login is still pending verification.', 'error');
          return;
        case 'logged in':
          goToDashboard('Already signed in elsewhere. Carry on.', null, username);
          return;
        case 'account is locked':
          setStatus(
            'login',
            'Account locked after too many failed codes. Contact support (us, on Twitter).',
            'error'
          );
          return;
        case 'password_locked':
          // Too many wrong passwords — an unlock code was emailed to the owner.
          if (json.login_attempt_id) {
            pendingAuthRef.current = { username, loginAttemptId: json.login_attempt_id };
            setDemoOtp(json.demo_otp || null);
            setStatus('login', 'Too many failed attempts — check your email for an unlock code.', 'error');
            window.setTimeout(() => showView('twofa'), 800);
          } else {
            setStatus('login', 'Account locked due to too many failed attempts. Check your email for an unlock code.', 'error');
          }
          return;
        case 'user not found':
          setStatus('login', 'Invalid username or password.', 'error');
          return;
        default:
          setStatus('login', json.message || 'Login failed. Try again.', 'error');
          attachCapture(loginPasswordRef.current);
      }
    } catch (err) {
      setStatus('login', `Network error: ${err.message}`, 'error');
      attachCapture(loginPasswordRef.current);
    }
  };

  const handleTwofa = async (ev) => {
    ev.preventDefault();
    const code = twofaCodeRef.current.value.trim();
    const pendingAuth = pendingAuthRef.current;

    if (!pendingAuth.username || !pendingAuth.loginAttemptId) {
      setStatus('twofa', 'No pending login. Start over.', 'error');
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      setStatus('twofa', 'Enter the 6-digit code.', 'error');
      return;
    }

    setStatus('twofa', 'Verifying...');
    try {
      const { json } = await api('/code_verification', {
        username: pendingAuth.username,
        login_attempt_id: pendingAuth.loginAttemptId,
        code
      });

      if (json.status === 'unlocked') {
        // This was a password-unlock verification, not a real login.
        // Clear pending state and send the user back to sign in normally.
        pendingAuthRef.current = { username: null, loginAttemptId: null };
        setStatus('twofa', 'Account unlocked. Please sign in again.', 'success');
        window.setTimeout(() => showView('login'), 1200);
        return;
      }
      if (json.status === 'accepted') {
        const verifiedUsername = pendingAuth.username;
        pendingAuthRef.current = { username: null, loginAttemptId: null };
        const message = json.enrolled
          ? 'Verified. You are now fully enrolled.'
          : 'Verified. Keep going - every clean login enrolls another sample.';
        goToDashboard(message, json, verifiedUsername);
        return;
      }
      if (json.message === 'max attempts exceeded') {
        setStatus('twofa', 'Max attempts exceeded. Account locked.', 'error');
        return;
      }
      if (json.message === 'expired') {
        setStatus('twofa', 'Code expired. Resend a new one.', 'error');
        return;
      }
      if (json.message === 'invalid attempt') {
        setStatus('twofa', 'This login attempt is no longer valid.', 'error');
        return;
      }
      setStatus('twofa', 'Wrong code. Try again.', 'error');
    } catch (err) {
      setStatus('twofa', `Network error: ${err.message}`, 'error');
    }
  };

  const handleResend = async (ev) => {
    ev.preventDefault();
    const pendingAuth = pendingAuthRef.current;
    if (!pendingAuth.username || !pendingAuth.loginAttemptId) {
      setStatus('twofa', 'No pending login.', 'error');
      return;
    }

    setStatus('twofa', 'Sending a new code...');
    try {
      const { json } = await api('/resend_code', {
        username: pendingAuth.username,
        login_attempt_id: pendingAuth.loginAttemptId
      });
      if (json.status === 'code sent') {
        setDemoOtp(json.demo_otp || null);
        setStatus('twofa', json.demo_otp ? 'New code generated.' : 'New code sent.', 'success');
      } else {
        setStatus('twofa', json.message || 'Could not resend.', 'error');
      }
    } catch (err) {
      setStatus('twofa', `Network error: ${err.message}`, 'error');
    }
  };

  const handleLogout = async () => {
    const username = activeUsername;
    pendingAuthRef.current = { username: null, loginAttemptId: null };
    setActiveUsername(null);
    setEnrollment(null);

    if (username) {
      try {
        await api('/logout', { username });
      } catch (err) {
        console.warn('[cadence] logout failed', err);
      }
    }

    showView('landing');
  };

  return (
    <>
      <div className="bg-gradient" aria-hidden="true"></div>
      <div className="bg-grid" aria-hidden="true"></div>

      <header className="nav">
        <a className="brand" href="/" onClick={routeTo('landing')}>
          <span className="brand-mark">◈</span>
          <span className="brand-name">Synergyze</span>
          <span className="brand-tld">.ai</span>
        </a>
        <nav className="nav-links">
          <a href="/#features">Features</a>
          <a href="/#social">Trusted By</a>
          <a href="/#pricing">Pricing</a>
          <a href="/#manifesto">Manifesto</a>
        </nav>
        <div className="nav-cta">
          <a className="btn btn-ghost" href="/login" onClick={routeTo('login')}>Sign in</a>
          <a className="btn btn-primary" href="/register" onClick={routeTo('register')}>
            Start free trial
          </a>
        </div>
      </header>

      <main id="app">
        <section className="route route-landing" data-route-view="landing" hidden={view !== 'landing'}>
          <div className="hero">
            <div className="badge">
              <span className="badge-dot"></span>
              Series Pre-Seed · backed by your uncle
            </div>

            <h1 className="hero-title">
              Unlock <em>10×</em> synergy with the world's first{' '}
              <span className="gradient-text">AI-native quantum thought-leadership platform.</span>
            </h1>

            <p className="hero-sub">
              Synergyze leverages a proprietary blockchain-agnostic LLM mesh to transform your team's
              vibes into actionable paradigms. No code. No users. No problem.
            </p>

            <div className="hero-cta">
              <a className="btn btn-primary btn-lg" href="/register" onClick={routeTo('register')}>
                Get started free <span className="arrow">→</span>
              </a>
              <a className="btn btn-ghost btn-lg" href="/login" onClick={routeTo('login')}>
                Sign in
              </a>
            </div>

            <ul className="hero-stats">
              <li>
                <strong>0</strong>
                <span>Fortune 500 customers</span>
              </li>
              <li>
                <strong>∞</strong>
                <span>Synergistic outcomes / mo</span>
              </li>
              <li>
                <strong>1.21<small>GW</small></strong>
                <span>Of pure ideation</span>
              </li>
              <li>
                <strong>9.8s</strong>
                <span>Avg time to disruption</span>
              </li>
            </ul>
          </div>

          <section className="logos" id="social" aria-label="As seen in">
            <p className="logos-label">As featured nowhere, allegedly</p>
            <div className="logos-row">
              <span>TechCrunch†</span>
              <span>Forbes 30 Under 90</span>
              <span>Y Comb&shy;inator (Rejected)</span>
              <span>Hacker News (Flagged)</span>
              <span>Product Hunt #847</span>
            </div>
          </section>

          <section className="features" id="features">
            <header className="section-head">
              <p className="eyebrow">The platform</p>
              <h2>One stack. Zero use cases. Infinite synergy.</h2>
            </header>

            <div className="feature-grid">
              <article className="feature-card">
                <div className="feature-icon">⚛︎</div>
                <h3>Quantum Synergy Engine™</h3>
                <p>
                  Our patent-pending engine collapses your team's wave function into a single
                  low-resolution Slack message. Schrödinger's standup, finally solved.
                </p>
              </article>

              <article className="feature-card">
                <div className="feature-icon">⌬</div>
                <h3>AI-First Disruption Layer</h3>
                <p>
                  Disrupt your industry by disrupting your own roadmap. Our LLM-powered pivot
                  generator ships a new strategy every 14 minutes, whether you asked or not.
                </p>
              </article>

              <article className="feature-card">
                <div className="feature-icon">⛓︎</div>
                <h3>Blockchain-Agnostic Workflows</h3>
                <p>
                  We don't use blockchain. We don't <em>not</em> use blockchain. Investors love
                  this slide, and so will you.
                </p>
              </article>

              <article className="feature-card">
                <div className="feature-icon">◉</div>
                <h3>Vibe-Driven Development</h3>
                <p>
                  Replace your specs, tickets, and engineers with a single Notion page that just
                  says "make it pop." Powered by GPT and good feelings.
                </p>
              </article>

              <article className="feature-card">
                <div className="feature-icon">⌁</div>
                <h3>Realtime Paradigm Shifter</h3>
                <p>
                  Detects when your competitors raise a Series B and automatically rewrites your
                  homepage to claim you did it first.
                </p>
              </article>

              <article className="feature-card">
                <div className="feature-icon">∞</div>
                <h3>Infinite Scale, Finite Bugs</h3>
                <p>
                  Engineered to scale to 10 billion users, currently serving three. Two of them are
                  co-founders. One is a bot.
                </p>
              </article>
            </div>
          </section>

          <section className="quote">
            <blockquote>
              "Synergyze didn't just transform our company. It transformed our company{' '}
              <em>twice</em>, then refunded our money, then charged us again. Honestly, the
              velocity is unmatched."
            </blockquote>
            <footer>
              <strong>Chad McThoughtleader</strong>
              <span>VP of Innovation, TBD&nbsp;Corp&nbsp;(stealth)</span>
            </footer>
          </section>

          <section className="cta-band" id="pricing">
            <h2 id="manifesto">A manifesto, of sorts.</h2>
            <p>
              We believe in synergy. We believe in disruption. We believe the words "synergy" and
              "disruption" can be combined to form "syndruption," and that the word "syndruption"
              is, itself, a kind of synergy. We are not currently accepting customers.
            </p>
            <p className="cta-meta">
              - The Synergyze Founding Team (one person, two LinkedIn profiles)
            </p>
          </section>

          <footer className="site-footer">
            <p className="powered-by">
              <span className="powered-mark">◈</span>
              Auth genuinely powered by{' '}
              <a href="https://github.com/" target="_blank" rel="noopener noreferrer">
                <strong>Cadence</strong>
              </a>{' '}
              - the only working piece of technology in this entire product.
            </p>
            <small>
              © 2026 Synergyze, Inc. † not actually featured. Patent pending&nbsp;(rejected). All
              rights reserved (some).
            </small>
          </footer>
        </section>

        <section className="route route-auth" data-route-view="register" hidden={view !== 'register'}>
          <div className="auth-card">
            <a className="auth-back" href="/" onClick={routeTo('landing')}>← back to home</a>
            <h1 className="auth-title">Create your account</h1>
            <p className="auth-sub">Join the dozens of teams (citation needed) already synergizing.</p>

            <form className="auth-form" id="register-form" autoComplete="off" noValidate onSubmit={handleRegister}>
              <label className="field">
                <span className="field-label">Work email</span>
                <input
                  type="email"
                  name="email"
                  id="register-email"
                  placeholder="ceo@your-startup.ai"
                  autoComplete="off"
                  required
                  ref={registerEmailRef}
                />
              </label>

              <label className="field">
                <span className="field-label">Username</span>
                <input
                  type="text"
                  name="username"
                  id="register-username"
                  placeholder="thought.leader.42"
                  autoComplete="username"
                  required
                  ref={registerUsernameRef}
                />
              </label>

              <label className="field">
                <span className="field-label">Password</span>
                <input
                  type="password"
                  name="password"
                  id="register-password"
                  placeholder="••••••••••••"
                  autoComplete="new-password"
                  data-lpignore="true"
                  data-1p-ignore="true"
                  required
                  ref={registerPasswordRef}
                />
                <ul className="field-hint">
                  <li>At least 8 characters</li>
                  <li>One uppercase letter (A–Z)</li>
                  <li>One lowercase letter (a–z)</li>
                  <li>One number (0–9)</li>
                  <li>One special character (!@#$…)</li>
                  <li>Type naturally — Cadence uses your keystroke rhythm as a second factor</li>
                </ul>
              </label>

              <button type="submit" className="btn btn-primary btn-block">
                Create account <span className="arrow">→</span>
              </button>

              <Status value={statuses.register} />

              <p className="auth-switch">
                Already optimizing? <a href="/login" onClick={routeTo('login')}>Sign in</a>
              </p>
            </form>

            <div className="auth-aside">
              <p className="aside-eyebrow">By signing up you agree to:</p>
              <ul>
                <li>Receive 4-11 emails per day from Chad</li>
                <li>Be invited to a Discord you'll never visit</li>
                <li>Let Cadence (an external thing, not us) read your typing rhythm</li>
              </ul>
              <p className="powered-by powered-by-aside">
                <span className="powered-mark">◈</span> Powered by Cadence
              </p>
            </div>
          </div>
        </section>

        <section className="route route-auth" data-route-view="login" hidden={view !== 'login'}>
          <div className="auth-card">
            <a className="auth-back" href="/" onClick={routeTo('landing')}>← back to home</a>
            <h1 className="auth-title">Welcome back, synergist</h1>
            <p className="auth-sub">Sign in with your password. Cadence handles the rest.</p>

            <form className="auth-form" id="login-form" autoComplete="off" noValidate onSubmit={handleLogin}>
              <label className="field">
                <span className="field-label">Username</span>
                <input
                  type="text"
                  name="username"
                  id="login-username"
                  placeholder="thought.leader.42"
                  autoComplete="username"
                  required
                  ref={loginUsernameRef}
                />
              </label>

              <label className="field">
                <span className="field-label">Password</span>
                <input
                  type="password"
                  name="password"
                  id="login-password"
                  placeholder="••••••••••••"
                  autoComplete="new-password"
                  data-lpignore="true"
                  data-1p-ignore="true"
                  required
                  ref={loginPasswordRef}
                />
                <span className="field-hint">
                  Type the way you always do. Cadence watches the rhythm, not the characters.
                </span>
              </label>

              <button type="submit" className="btn btn-primary btn-block">
                Sign in <span className="arrow">→</span>
              </button>

              <Status value={statuses.login} />

              <p className="auth-switch">
                No account yet? <a href="/register" onClick={routeTo('register')}>Start free trial</a>
              </p>
            </form>

            <div className="auth-aside">
              <p className="aside-eyebrow">Why two factors?</p>
              <ul>
                <li>Passwords leak. Your typing rhythm doesn't.</li>
                <li>If Cadence isn't sure it's you, it'll email a one-time code.</li>
                <li>Five clean logins and you're fully enrolled.</li>
              </ul>
              <p className="powered-by powered-by-aside">
                <span className="powered-mark">◈</span> Powered by Cadence
              </p>
            </div>
          </div>
        </section>

        <section className="route route-auth" data-route-view="twofa" hidden={view !== 'twofa'}>
          <div className="auth-card">
            <a className="auth-back" href="/login" onClick={routeTo('login')}>← back to sign in</a>
            <h1 className="auth-title">Verify it's you</h1>
            <p className="auth-sub">
              {demoOtp
                ? "Use the 6-digit code shown below. It's good for 5 minutes."
                : "We sent a 6-digit code to your email. It's good for 5 minutes."}
            </p>
            <p className="auth-sub">Don't worry - we don't care how you type this part.</p>

            <div className="demo-banner" id="demo-banner" hidden={!demoOtp}>
              <span className="demo-banner-label">Demo mode</span>
              <span className="demo-banner-text">
                No email needed - your code is <strong id="demo-otp">{demoOtp || '000000'}</strong>
              </span>
            </div>

            <form className="auth-form" id="twofa-form" autoComplete="off" noValidate onSubmit={handleTwofa}>
              <label className="field">
                <span className="field-label">One-time code</span>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  name="code"
                  id="twofa-code"
                  placeholder="123456"
                  autoComplete="off"
                  required
                  ref={twofaCodeRef}
                />
              </label>

              <button type="submit" className="btn btn-primary btn-block">
                Verify <span className="arrow">→</span>
              </button>

              <Status value={statuses.twofa} />

              <p className="auth-switch">
                Need another one? <a href="#" id="twofa-resend" onClick={handleResend}>
                  {demoOtp ? 'Generate code' : 'Resend code'}
                </a>
              </p>
            </form>
          </div>
        </section>

        <section className="route route-auth" data-route-view="dashboard" hidden={view !== 'dashboard'}>
          <div className="auth-card">
            <h1 className="auth-title">You're synergized. ✦</h1>
            <p className="auth-sub" id="dashboard-sub">{dashboardSub}</p>

            <Enrollment payload={enrollment} />


            <button type="button" className="btn btn-ghost btn-block" id="logout-btn" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
