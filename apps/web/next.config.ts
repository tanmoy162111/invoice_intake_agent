import type { NextConfig } from "next";

const allowedOrigins = (process.env.ALLOWED_ORIGINS ?? "")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    serverActions: {
      // invoices go up to 15 MB; the API enforces the real limit
      bodySizeLimit: "16mb",
      // Behind a proxy that rewrites the Host header, list the public address(es) so actions still
      // pass Next's origin check without loosening it (ALLOWED_ORIGINS=app.example.com,...).
      ...(allowedOrigins.length > 0 ? { allowedOrigins } : {}),
    },
  },
};

export default nextConfig;
