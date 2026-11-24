/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Scanner routes make outbound requests; keep them on the Node runtime.
  experimental: {},
};

export default nextConfig;
