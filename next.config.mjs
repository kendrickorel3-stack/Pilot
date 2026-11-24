/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Scanner routes make outbound requests and drive headless Chromium; keep them
  // on the Node runtime and don't bundle playwright into the serverless trace.
  experimental: {
    serverComponentsExternalPackages: ["playwright", "playwright-core", "chromium-bidi"],
  },
};

export default nextConfig;
