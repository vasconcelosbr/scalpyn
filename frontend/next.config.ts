import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent Vercel/Next.js from stripping trailing slashes.
  // Without this, /api/pools/ becomes /api/pools, then Cloud Run redirects back with http:// → Mixed Content error.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    const backendBase = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        // Explicitly include /api in the destination so BACKEND_URL is just the domain.
        // e.g., /api/pools/ → https://cloud-run.app/api/pools/
        destination: `${backendBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
