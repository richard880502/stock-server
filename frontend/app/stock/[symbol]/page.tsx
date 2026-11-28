import { StockPage } from "@/features/stock/stock-page";
import { symbolFromParam } from "@/lib/stock-routes";

export const metadata = { title: "個股 — Quant Signal" };

export default async function Page({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <StockPage symbol={symbolFromParam(symbol)} />;
}
