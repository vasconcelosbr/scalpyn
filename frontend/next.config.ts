import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // API proxying is handled by app/api/[...path]/route.ts (explicit proxy handler).
  // This is more reliable than rewrites for cross-origin proxying with trailing slashes.
  output: "standalone",
};


export default nextConfig;
