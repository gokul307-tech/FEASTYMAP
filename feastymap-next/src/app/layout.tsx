import type { Metadata } from 'next';
import 'leaflet/dist/leaflet.css';
import './style.css';

export const metadata: Metadata = {
  title: 'FeastyMap | Pondicherry outing planner',
  description: 'Build a practical Pondicherry outing with live place and route information.',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
