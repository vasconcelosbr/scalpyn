import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // API proxying is handled by app/api/[...path]/route.ts (explicit proxy handler).
  // This is more reliable than rewrites for cross-origin proxying with trailing slashes.

  // Habilita compressão gzip no servidor Next.js para HTML/JS/CSS/JSON servidos
  // diretamente (páginas SSR, API routes próprias do Next.js).
  compress: true,

  // Logging de rotas: útil para medir latência de proxy em dev.
  logging: {
    fetches: {
      fullUrl: false,
    },
  },
};

export default nextConfig;
