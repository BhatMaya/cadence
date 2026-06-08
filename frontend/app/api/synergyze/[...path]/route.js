const DEFAULT_API_BASE = 'http://localhost:5001';

function backendBaseUrl() {
  return (
    process.env.SYNERGYZE_API_BASE ||
    process.env.NEXT_PUBLIC_SYNERGYZE_API_BASE ||
    DEFAULT_API_BASE
  ).replace(/\/+$/, '');
}

async function proxy(request, { params }) {
  const resolvedParams = await params;
  const path = (resolvedParams.path || []).join('/');
  const requestUrl = new URL(request.url);
  const targetUrl = new URL(`${backendBaseUrl()}/${path}`);
  targetUrl.search = requestUrl.search;

  const headers = new Headers();
  const contentType = request.headers.get('content-type');
  if (contentType) headers.set('content-type', contentType);

  const apiKey = process.env.CADENCE_API_KEY;
  if (apiKey) headers.set('authorization', `Bearer ${apiKey}`);

  const method = request.method.toUpperCase();
  const hasBody = !['GET', 'HEAD'].includes(method);
  const upstream = await fetch(targetUrl, {
    method,
    headers,
    body: hasBody ? await request.text() : undefined,
    cache: 'no-store'
  });

  const responseHeaders = new Headers();
  const upstreamContentType = upstream.headers.get('content-type');
  if (upstreamContentType) responseHeaders.set('content-type', upstreamContentType);

  return new Response(await upstream.text(), {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders
  });
}

export async function GET(request, context) {
  return proxy(request, context);
}

export async function POST(request, context) {
  return proxy(request, context);
}

export async function PATCH(request, context) {
  return proxy(request, context);
}
