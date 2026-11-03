import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { themeInitScript } from "@/lib/theme";
import "./globals.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/charts.css";
import "./styles/views.css";
import "./styles/market.css";

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
    <html lang="zh-Hant" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
