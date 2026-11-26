import { redirect } from "next/navigation";

// 看盤 moved into the 自選股 page as a view.
export default function TerminalPage() {
  redirect("/watchlist?view=terminal");
}
