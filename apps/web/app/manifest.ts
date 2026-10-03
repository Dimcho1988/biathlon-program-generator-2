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
    background_color: "#07111d",
    theme_color: "#07111d",
    prefer_related_applications: false,
    icons: [
      { src: "/icons/onflows-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/onflows-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/onflows-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
