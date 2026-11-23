/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [{ source: "/connect/chatgpt", headers: [
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'; form-action 'self' https://chatgpt.com" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Frame-Options", value: "DENY" },
    ] }];
  },
};

export default nextConfig;
