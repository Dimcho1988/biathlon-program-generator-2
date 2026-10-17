"use client";
import { useMemo } from "react";
import type { ActivitySeriesPoint } from "../lib/activities";
import { MetricChart } from "./metric-chart";

export type ActivityChannel = { key: keyof ActivitySeriesPoint; label: string; color: string; unit: string };
export function ActivityTimelineChart({ title, series, channels }: { title: string; series: ActivitySeriesPoint[]; channels: ActivityChannel[] }) {
  const data = useMemo(() => channels.map(channel => ({ key: String(channel.key), label: channel.label, color: channel.color,
    points: series.filter(row => row.elapsed_s !== null && Number.isFinite(row.elapsed_s)).map(row => ({ x: row.elapsed_s!, y: typeof row[channel.key] === "number" ? row[channel.key] as number : null })),
  })), [channels, series]);
  const gaps = series.slice(1).flatMap((row, i) => row.elapsed_s !== null && series[i].elapsed_s !== null && row.elapsed_s > series[i].elapsed_s! ? [row.elapsed_s - series[i].elapsed_s!] : []).sort((a, b) => a - b);
  const typical = gaps[Math.floor(gaps.length / 2)] ?? 1;
  return <MetricChart title={title} series={data} unit={channels[0].unit} xKind="duration" maxGap={typical * 3} zero={channels[0].key === "speed_kmh"} />;
}
