import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Stem Borer — paywall security tester",
  description: "Find paywalled media that leaks on domains you own, and get the fix.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
