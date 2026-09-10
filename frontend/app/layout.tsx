import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "http://127.0.0.1:13000",
  ),
  title: "Quant Signal — Evidence-first market intelligence",
  description:
    "結合確定性量化訊號、異常偵測、LLM 情境分析與 point-in-time 回測的市場決策控制台。",
  openGraph: {
    title: "Quant Signal — Evidence-first market intelligence",
    description: "將市場雜訊整理成可驗證的決策條件。",
    images: [{ url: "/og.png", width: 1731, height: 909, alt: "Quant Signal 市場決策控制台" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Quant Signal — Evidence-first market intelligence",
    description: "將市場雜訊整理成可驗證的決策條件。",
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
