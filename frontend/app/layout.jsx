import "./globals.css";

export const metadata = {
  title: "Watchtower | Portal monitoring",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
