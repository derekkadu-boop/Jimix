import localFont from "next/font/local";
import "./globals.css";

const geist = localFont({
  src: "../node_modules/next/dist/next-devtools/server/font/geist-latin.woff2",
  weight: "100 900",
  variable: "--font-geist",
  display: "swap",
});

export const metadata = {
  title: "Watchtower | Patterno Operations",
  description: "Monitor European procurement portal health, automated repairs, and upcoming tender deadlines.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={geist.variable}>
      <body>{children}</body>
    </html>
  );
}
