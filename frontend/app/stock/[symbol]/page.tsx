import { StockPage } from "@/features/stock/stock-page";
import { stockTitle } from "@/features/stock/page/model";
import { symbolFromParam } from "@/lib/stock-routes";

export async function generateMetadata({ params }: { params: Promise<{ symbol: string }> }) {
  const symbol = symbolFromParam((await params).symbol);
  return { title: symbol ? stockTitle(symbol, null) : "個股 — Quant Signal" };
}

export default async function Page({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <StockPage symbol={symbolFromParam(symbol)} />;
}
