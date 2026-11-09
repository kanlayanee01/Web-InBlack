import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // nginx rewrites the HTML to add a per-request CSP nonce, which needs an uncompressed response.
  compress: false,
  async redirects() {
    return [{ source: "/", destination: "/th", permanent: false }];
  },
};

export default config;
