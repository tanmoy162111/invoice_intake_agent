import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // invoices go up to 15 MB; the API enforces the real limit
    serverActions: { bodySizeLimit: "16mb" },
  },
};

export default nextConfig;
