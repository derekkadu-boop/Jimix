const backend = new URL(process.env.JIMIX_API_URL || "http://127.0.0.1:8000").origin;

export default {
  output: process.env.JIMIX_STANDALONE === "1" ? "standalone" : undefined,
  // The development badge measures and resizes itself; keep it out of the embedded preview.
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/portals/:path*", destination: `${backend}/portals/:path*` },
      { source: "/openapi.json", destination: `${backend}/openapi.json` },
      { source: "/files/:path*", destination: `${backend}/files/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
};
