# Cadence Developer Console

A minimal but polished developer console for app developers who want to
integrate **Cadence** keystroke-dynamics authentication into their own
products. This is a standalone Next.js app — it is **separate** from the
Synergyze demo frontend and does not modify it.

With the console you can:

- Sign up / log in as a developer (Supabase-backed auth via the Cadence API)
- Create and browse your applications
- Issue and revoke server-side API keys (`sk_live_…`)
- View per-app usage metrics (keys, end users, typing samples, scoring)
- Read app-specific integration docs for `/v1/enroll` and `/v1/score`

## Tech stack

- [Next.js 16](https://nextjs.org/) (App Router) + React 19 client components
- No CSS framework — a single self-contained design system in
  `app/globals.css`
- Talks directly to the Cadence backend with the **developer access token**
  (never a server-side API key)

## Prerequisites

- Node.js 18.18+ (tested on Node 22)
- A running Cadence backend (defaults to `http://localhost:5001`)

> The Cadence backend allows the dev origin `http://localhost:3000` via CORS by
> default, which is the port this console runs on.

## Getting started

```bash
# 1. Install dependencies
npm install

# 2. Configure the API base URL
cp .env.example .env.local
# edit .env.local if your backend is not on http://localhost:5001

# 3. Run the dev server
npm run dev
```

Open <http://localhost:3000>.

### Production build

```bash
npm run build
npm run start
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable                          | Default                 | Description                          |
| --------------------------------- | ----------------------- | ------------------------------------ |
| `NEXT_PUBLIC_CADENCE_API_BASE_URL` | `http://localhost:5001` | Base URL of the Cadence backend API. |

Only `NEXT_PUBLIC_*` variables are exposed to the browser. The console performs
all authenticated requests with the developer access token returned by login,
sent as `Authorization: Bearer <developer-access-token>`.

## How it works

1. **Auth** — `POST /v1/developer/signup` and `POST /v1/developer/login`. The
   returned `session.access_token` is stored in `localStorage`. If signup
   returns `email_confirmed: false`, the UI shows a "check your email" state
   instead of logging you in. Logout clears stored credentials.
2. **Apps** — `GET /v1/developer/apps` lists apps;
   `POST /v1/developer/apps` creates one and returns its first API key. The full
   key is displayed **exactly once** in a copy-friendly modal.
3. **API keys** — list, create (`POST …/api-keys`), and revoke
   (`POST /v1/developer/api-keys/:id/revoke`). The list only ever shows key
   metadata and the prefix — never the full secret.
4. **Usage** — `GET /v1/developer/apps/:id/usage` powers the metrics dashboard.
5. **Integration docs** — a per-app panel with copyable `curl` examples for
   `/v1/enroll` and `/v1/score`, and guidance to keep `sk_live_` keys
   server-side only.

## Security notes

- The console uses the **developer access token** in the browser — this is the
  Supabase auth token for managing your account. It is **not** an `sk_live_`
  API key.
- Server-side API keys (`sk_live_…`) must **never** be embedded in browser or
  mobile client code. Browser apps should capture typing samples client-side,
  send them to their own backend, and have that backend call Cadence with the
  API key.

## Project structure

```
developer-console/
├── app/
│   ├── globals.css        # self-contained design system
│   ├── layout.js          # root layout + AuthProvider
│   ├── page.js            # auth gate → AuthScreen or Console
│   └── auth-context.js    # client auth context (token + developer)
├── components/
│   ├── AuthScreen.js       # login / signup, email-confirmation state
│   ├── Console.js          # topbar + sidebar app list + main panel
│   ├── AppDetail.js        # tabbed detail (Usage / Keys / Integrate)
│   ├── CreateAppModal.js
│   ├── KeyRevealModal.js   # one-time full-key reveal
│   ├── ApiKeysPanel.js
│   ├── UsagePanel.js
│   ├── IntegrationDocs.js
│   └── CopyButton.js
├── lib/
│   ├── api.js             # typed fetch client for all developer endpoints
│   ├── config.js          # API base URL + storage keys
│   ├── storage.js         # SSR-safe localStorage session helpers
│   └── format.js          # number/percent/latency/date formatters
├── .env.example
└── README.md
```

## Extending

- Add a new endpoint: add a function in `lib/api.js`, then consume it from a
  component.
- Add a new metric: it flows automatically once the backend includes it in the
  `usage` payload — extend `UsagePanel.js`.
- Styling tokens live as CSS variables at the top of `app/globals.css`.
