import { expect, it } from "vitest";
import manifest from "../app/manifest";
import AppleIcon, { size as appleSize, contentType } from "../app/apple-icon";
import { GET, generateStaticParams } from "../app/icons/[icon]/route";

it("defines a stable public standalone app identity with 192/512 and separate maskable icons", () => {
  const value = manifest();
  expect(value.id).toBe("/"); expect(value.scope).toBe("/"); expect(value.start_url).toBe("/");
  expect(value.name).toBe("onFlows"); expect(value.display).toBe("standalone");
  expect(value.prefer_related_applications).toBe(false);
  expect(value.icons?.filter(icon => icon.purpose === "any").map(icon => icon.sizes)).toEqual(["192x192", "512x512"]);
  expect(value.icons?.find(icon => icon.purpose === "maskable")?.sizes).toBe("512x512");
  expect(value.icons?.every(icon => icon.src.startsWith("/icons/") && !icon.src.includes("?"))).toBe(true);
  expect(generateStaticParams().map(params => `/icons/${params.icon}`)).toEqual(value.icons?.map(icon => icon.src));
});

function dimensions(buffer: ArrayBuffer) {
  const data = Buffer.from(buffer);
  expect(data.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}

it.each(generateStaticParams())("renders public $icon as a correctly sized PNG without remote assets or account data", async ({ icon }) => {
  const response = await GET(new Request(`https://onflows.test/icons/${icon}`), { params: Promise.resolve({ icon }) });
  const width = icon.includes("192") ? 192 : 512;
  expect(response.status).toBe(200); expect(response.headers.get("content-type")).toContain("image/png");
  expect(response.headers.get("cache-control")).toContain("public");
  expect(dimensions(await response.arrayBuffer())).toEqual({ width, height: width });
});

it("provides the Apple touch icon and refuses unlisted icon names", async () => {
  expect(contentType).toBe("image/png");
  expect(dimensions(await (await AppleIcon()).arrayBuffer())).toEqual(appleSize);
  expect((await GET(new Request("https://onflows.test/icons/private.png"), { params: Promise.resolve({ icon: "private.png" }) })).status).toBe(404);
});
