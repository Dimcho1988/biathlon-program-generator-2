import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "onFlows",
    short_name: "onFlows",
    description: "Тренировъчен дневник, анализ на натоварването и индивидуално планиране.",
    lang: "bg",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    prefer_related_applications: false,
    icons: [
      { src: "/icons/onflows-white-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/onflows-white-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/onflows-white-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
