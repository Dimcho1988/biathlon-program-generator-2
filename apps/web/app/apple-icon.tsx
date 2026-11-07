import { appIcon } from "../lib/app-icon";

// White-background revision also changes Next's metadata URL for cached touch icons.

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return appIcon(size.width);
}
