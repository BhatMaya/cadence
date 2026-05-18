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

function Status({ value }) {
  const kind = value?.kind ? ` is-${value.kind}` : '';
  return <p className={`auth-meta${kind}`} data-form-status>{value?.message ?? ''}</p>;
}

function Enrollment({ payload }) {
  if (
    !payload ||
    typeof payload.enrollment_required !== 'number' ||
    typeof payload.enrollment_count !== 'number'
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

  const registerEmailRef = useRef(null);
  const registerUsernameRef = useRef(null);
  const registerPasswordRef = useRef(null);
  const loginUsernameRef = useRef(null);
  const loginPasswordRef = useRef(null);
  const twofaCodeRef = useRef(null);
  const activeCaptureRef = useRef(null);
  const pendingAuthRef = useRef({ username: null, loginAttemptId: null });

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
    activeCaptureRef.current = {
      session,
      finalize() {
        lastSample = null;
        session.stop();
        return lastSample;
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

  const goToDashboard = useCallback((message, payload) => {
    if (message) setDashboardSub(message);
    setEnrollment(payload);
    showView('dashboard');
  }, [showView]);

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
    if (view !== 'twofa') setDemoOtp(null);
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

    const sample = activeCaptureRef.current ? activeCaptureRef.current.finalize() : null;
    const raw_data = sample ? { events: sample.events } : { events: [] };

    setStatus('login', 'Analyzing your typing rhythm...');
    try {
      const { json } = await api('/authenticate', { username, password, raw_data });

      switch (json.status) {
        case 'accepted':
          goToDashboard('Welcome back. The paradigm is yours.', json);
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
              : 'Your typing rhythm looked off. Check your email for a code.';
          setStatus('login', reasonNote, 'success');
          window.setTimeout(() => showView('twofa'), 600);
          return;
        }
        case 'pending 2fa':
          setStatus('login', 'A previous login is still pending verification.', 'error');
          return;
        case 'logged in':
          goToDashboard('Already signed in elsewhere. Carry on.', null);
          return;
        case 'account is locked':
          setStatus(
            'login',
            'Account locked after too many failed codes. Contact support (us, on Twitter).',
            'error'
          );
          return;
        case 'user not found':
          setStatus('login', 'No account with that username.', 'error');
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

      if (json.status === 'accepted') {
        pendingAuthRef.current = { username: null, loginAttemptId: null };
        const message = json.enrolled
          ? 'Verified. You are now fully enrolled.'
          : 'Verified. Keep going - every clean login enrolls another sample.';
        goToDashboard(message, json);
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
        setStatus('twofa', 'New code sent.', 'success');
      } else {
        setStatus('twofa', json.message || 'Could not resend.', 'error');
      }
    } catch (err) {
      setStatus('twofa', `Network error: ${err.message}`, 'error');
    }
  };

  const handleLogout = () => {
    pendingAuthRef.current = { username: null, loginAttemptId: null };
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
                  required
                  ref={registerPasswordRef}
                />
                <span className="field-hint">
                  Minimum 8 characters. Type naturally - Cadence will use your keystroke rhythm as a
                  second factor on sign in.
                </span>
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
                  required
                  ref={loginPasswordRef}
                />
                <span className="field-hint">
                  Type the way you always do. Cadence watches the rhythm, not the characters -
                  Synergyze couldn't build this if we tried.
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
            <p className="auth-sub">We sent a 6-digit code to your email. It's good for 5 minutes.</p>

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
                  required
                  ref={twofaCodeRef}
                />
              </label>

              <button type="submit" className="btn btn-primary btn-block">
                Verify <span className="arrow">→</span>
              </button>

              <Status value={statuses.twofa} />

              <p className="auth-switch">
                Didn't get it? <a href="#" id="twofa-resend" onClick={handleResend}>Resend code</a>
              </p>
            </form>
          </div>
        </section>

        <section className="route route-auth" data-route-view="dashboard" hidden={view !== 'dashboard'}>
          <div className="auth-card">
            <h1 className="auth-title">You're synergized. ✦</h1>
            <p className="auth-sub" id="dashboard-sub">{dashboardSub}</p>

            <Enrollment payload={enrollment} />

            <div className="auth-aside">
              <p className="aside-eyebrow">What now?</p>
              <ul>
                <li>Forward your salary to a Substack</li>
                <li>Pivot, but quietly</li>
                <li>Sign out and tell a friend (please)</li>
              </ul>
            </div>

            <button type="button" className="btn btn-ghost btn-block" id="logout-btn" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </section>
      </main>
    </>
  );
}
