import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Pin the workspace root to this project so Next.js doesn't pick up a
  // parent lockfile when inferring the root.
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
