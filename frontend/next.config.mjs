const backend = new URL(process.env.JIMIX_API_URL || "http://127.0.0.1:8000").origin;

export default {
  async rewrites() {
    return [
      { source: "/portals/:path*", destination: `${backend}/portals/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
};
