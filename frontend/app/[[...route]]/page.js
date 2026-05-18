import SynergyzeApp from '../../components/SynergyzeApp.js';

const ROUTES = new Set(['landing', 'register', 'login', 'twofa', 'dashboard']);

export default async function Page({ params }) {
  const routeParts = (await params)?.route ?? [];
  const route = routeParts[0] ?? 'landing';
  const initialRoute = ROUTES.has(route) ? route : 'landing';

  return <SynergyzeApp initialRoute={initialRoute} />;
}
