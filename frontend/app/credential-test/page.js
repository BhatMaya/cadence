import Link from 'next/link';

export const metadata = {
  title: 'Credential timing test retired - Synergyze',
  description: 'The old local Cadence model scoring page has been retired.'
};

export default function Page() {
  return (
    <main style={{
      minHeight: '100vh',
      display: 'grid',
      placeItems: 'center',
      padding: '2rem',
      background: '#f8fafc',
      color: '#111827'
    }}>
      <section style={{
        width: 'min(100%, 42rem)',
        border: '1px solid #d1d5db',
        borderRadius: '8px',
        padding: '2rem',
        background: '#ffffff',
        boxShadow: '0 16px 48px rgba(15, 23, 42, 0.08)'
      }}>
        <p style={{
          margin: '0 0 0.75rem',
          color: '#6b7280',
          fontSize: '0.875rem',
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0'
        }}>
          Retired test page
        </p>
        <h1 style={{ margin: '0 0 1rem', fontSize: '2rem', lineHeight: 1.15 }}>
          Credential timing tests now run through the real auth flow.
        </h1>
        <p style={{ margin: '0 0 1.5rem', lineHeight: 1.6, color: '#374151' }}>
          The old standalone scoring route was removed after Cadence moved to
          app-scoped signup, login, recovery, and dashboard flows.
        </p>
        <Link
          href="/"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '44px',
            padding: '0 1rem',
            borderRadius: '6px',
            background: '#111827',
            color: '#ffffff',
            fontWeight: 700,
            textDecoration: 'none'
          }}
        >
          Open Synergyze
        </Link>
      </section>
    </main>
  );
}
