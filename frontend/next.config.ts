import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prevent Vercel/Next.js from stripping trailing slashes.
  // Without this, /api/pools/ becomes /api/pools, then Cloud Run (which uses
  // redirect_slashes=True) redirects back with http:// → Mixed Content error.
  skipTrailingSlashRedirect: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL ?? "http://localhost:8000/api"}/:path*`,
      },
    ];
  },
};

export default nextConfig;
