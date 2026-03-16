import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent Vercel from stripping trailing slashes before Next.js rewrites handle them.
  // Without this, /api/pools/ → /api/pools → Cloud Run redirects back with http:// = Mixed Content.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // NOTE: BACKEND_URL already includes /api (e.g. https://...run.app/api)
        // so the destination is BACKEND_URL/:path* = https://...run.app/api/pools/
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8000/api"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
