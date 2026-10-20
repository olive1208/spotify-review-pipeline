import "./globals.css";

export const metadata = {
  title: "Spotify Review Priorities",
  description: "Where should the next quarter of product effort go? Evidence from classified Spotify reviews.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
