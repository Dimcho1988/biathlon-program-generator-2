import { appIcon } from "../../../lib/app-icon";

export const dynamic = "force-static";
export const dynamicParams = false;
const icons = {
  "onflows-192.png": { size: 192, maskable: false },
  "onflows-512.png": { size: 512, maskable: false },
  "onflows-maskable-512.png": { size: 512, maskable: true },
} as const;

export function generateStaticParams() {
  return Object.keys(icons).map(icon => ({ icon }));
}

export async function GET(_request: Request, { params }: { params: Promise<{ icon: string }> }) {
  const { icon } = await params;
  if (!Object.hasOwn(icons, icon)) return new Response(null, { status: 404 });
  const selected = icons[icon as keyof typeof icons];
  return appIcon(selected.size, selected.maskable);
}
