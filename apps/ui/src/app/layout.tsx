import type { Metadata, Viewport } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "EMMA · QuantumLoopAI Voice Assistant",
  description:
    "EMMA — a minimal, premium voice-agent interface by QuantumLoopAI. Talk to the orb.",
};

export const viewport: Viewport = {
  themeColor: "#050505",
  viewportFit: "cover",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} dark h-full antialiased`}
    >
      <body className="bg-[#050505] min-h-full text-white">{children}</body>
    </html>
  );
}
