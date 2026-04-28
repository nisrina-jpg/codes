/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",   // ← this enables GitHub Pages compatibility
  images: { unoptimized: true },
}

module.exports = nextConfig

const res = await fetch(
  `${process.env.NEXT_PUBLIC_API_BASE}/scrape-cached`
);
const data = await res.json();