import { WatchlistTable } from "@/components/watchlist/WatchlistTable";

export default function WatchlistPage() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[#E2E8F0]">Market Watchlist</h1>
          <p className="text-[#94A3B8] mt-1 text-sm">Real-time Alpha Score rankings and technical indicators.</p>
        </div>
      </div>
      
      <WatchlistTable />
    </div>
  );
}
