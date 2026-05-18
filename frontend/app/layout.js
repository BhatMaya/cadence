import './globals.css';

export const metadata = {
  title: 'Synergyze - The AI-powered synergy platform',
  description: 'A mock SaaS frontend that exercises Cadence authentication.'
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
