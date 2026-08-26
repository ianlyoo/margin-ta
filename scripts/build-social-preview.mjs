import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const PRODUCT_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_HTML = join(PRODUCT_ROOT, "docs", "social-preview.html");
const OUT_PNG = join(PRODUCT_ROOT, "docs", "assets", "social-preview.png");
const WIDTH = 1280;
const HEIGHT = 640;

function buildSvg() {
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="margin-ta social preview">
  <rect width="${WIDTH}" height="${HEIGHT}" fill="#0f172a"/>
  <text x="72" y="220" font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, Noto Sans, sans-serif" font-size="64" font-weight="800" letter-spacing="-1.5" fill="#ffffff">margin-ta</text>
  <text x="72" y="278" font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, Noto Sans, sans-serif" font-size="26" font-weight="500" fill="#e2e8f0">Read-only technical-analysis pipeline &#8212; multi-horizon stances with indicator consensus</text>
  <rect x="72" y="308" width="420" height="38" rx="6" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
  <text x="86" y="333" font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, Noto Sans, sans-serif" font-size="16" font-weight="700" letter-spacing="0.6" fill="#0f172a">measured workloads &#8212; not guaranteed</text>
  <text x="72" y="388" font-family="system-ui, -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, Noto Sans, sans-serif" font-size="15" fill="#94a3b8">github.com/ianlyoo/margin-ta  \u00B7  MIT  \u00B7  Python</text>
</svg>`;
}

async function main() {
  mkdirSync(join(PRODUCT_ROOT, "docs", "assets"), { recursive: true });
  const html = readFileSync(SRC_HTML, "utf8");
  if (!html.includes("margin-ta")) throw new Error("template missing margin-ta");
  if (!html.includes("measured workloads")) throw new Error("template missing cue");
  const svg = buildSvg();
  const { createRequire } = await import("node:module");
  const require = createRequire(import.meta.url);
  const sharp = require("C:\\Users\\torch\\Documents\\GeminiContextPack\\node_modules\\sharp");
  const png = await sharp(Buffer.from(svg, "utf8")).png({ compressionLevel: 9, adaptiveFiltering: false, palette: false }).toBuffer();
  const meta = await sharp(png).metadata();
  if (meta.width !== WIDTH || meta.height !== HEIGHT) throw new Error(`dims ${meta.width}x${meta.height}`);
  if (png.length >= 1024*1024) throw new Error(`PNG ${png.length} >=1MiB`);
  writeFileSync(OUT_PNG, png);
  const sha = createHash("sha256").update(png).digest("hex");
  console.log(`[social-preview] wrote ${OUT_PNG} ${WIDTH}x${HEIGHT} ${png.length} bytes sha256:${sha}`);
}
await main();
