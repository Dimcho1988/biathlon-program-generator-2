/* eslint-disable @next/next/no-img-element -- ImageResponse uses server-rendered SVG, not next/image. */
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";

let markData: Promise<string> | undefined;
function readMark() {
  markData ??= readFile(join(process.cwd(), "public/brand/onflows-mark.png"))
    .then(bytes => `data:image/png;base64,${bytes.toString("base64")}`);
  return markData;
}

export async function appIcon(size: number, maskable = false) {
  const src = await readMark();
  // The entire maskable image rectangle fits inside the central safe circle:
  // sqrt((.61 / 2)^2 + (.61 * 922 / 1121 / 2)^2) < .4.
  // This guarantee does not depend on the source's transparent edge.
  const height = Math.round(size * (maskable ? .61 : .84));
  const width = Math.round(height * 922 / 1121);
  return new ImageResponse(
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "#07111d" }}>
      <img src={src} width={width} height={height} alt="" />
    </div>,
    { width: size, height: size, headers: { "Cache-Control": "public, max-age=86400" } },
  );
}
