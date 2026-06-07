import "./globals.css";
import { AuthProvider } from "./auth-context";

export const metadata = {
  title: "Cadence Developer Console",
  description:
    "Integrate Cadence keystroke-dynamics authentication into your own apps. Manage apps, API keys, and usage.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0b0f",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
