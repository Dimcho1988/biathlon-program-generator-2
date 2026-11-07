"use client";

import { useCallback, useEffect, useState } from "react";

// Coordinates are CSS pixels at the measured container width. Labels do not shrink on phones.
export function useChartLayout(left = 58, height = 284) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => setElement(node), []);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    if (!element) return;
    const measure = () => { const size = element.getBoundingClientRect().width; if (size > 0) setWidth(Math.max(220, Math.round(size))); };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  const right = width - 20, top = 32, bottom = height - 44;
  return { ref, width, height, left, right, top, bottom, plotWidth: right - left, plotHeight: bottom - top, compact: width < 480 };
}
