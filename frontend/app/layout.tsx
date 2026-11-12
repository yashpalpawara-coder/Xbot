import type { Metadata } from "next";
import { Fraunces, Outfit } from "next/font/google";

import { Shell } from "@/components/shell";
import "./globals.css";

const sans = Outfit({ subsets: ["latin"], variable: "--font-sans" });
const serif = Fraunces({ subsets: ["latin"], variable: "--font-serif" });

export const metadata: Metadata = {
  title: "XBot",
  description: "Find public posts, analyse them, and publish one approved reply.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sans.className} ${sans.variable} ${serif.variable}`}>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
