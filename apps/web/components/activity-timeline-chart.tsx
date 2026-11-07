"use client";
import { useMemo } from "react";
import type { ActivitySeriesPoint } from "../lib/activities";
import { MetricChart } from "./metric-chart";
import { useRunningSpeedUnit } from "./running-speed-unit";
import { isRunningSport } from "../lib/speed-format";

export type ActivityChannel = { key: keyof ActivitySeriesPoint; label: string; color: string; unit: string };
export function ActivityTimelineChart({ title, series, channels, sport }: { title: string; series: ActivitySeriesPoint[]; channels: ActivityChannel[]; sport?: string }) {
  const unit = useRunningSpeedUnit();
  const pace = Boolean(sport && isRunningSport(sport) && unit === "pace" && channels.length === 1 && channels[0].key === "speed_kmh");
  const data = useMemo(() => channels.map(channel => ({ key: String(channel.key), label: pace ? "Темпо" : channel.label, color: channel.color,
    points: series.filter(row => row.elapsed_s !== null && Number.isFinite(row.elapsed_s)).map(row => {const value = row[channel.key];return { x: row.elapsed_s!, y: typeof value !== "number" ? null : pace ? value > 0 ? 3600 / value : null : value };}),
  })), [channels, series, pace]);
  const gaps = series.slice(1).flatMap((row, i) => row.elapsed_s !== null && series[i].elapsed_s !== null && row.elapsed_s > series[i].elapsed_s! ? [row.elapsed_s - series[i].elapsed_s!] : []).sort((a, b) => a - b);
  const typical = gaps[Math.floor(gaps.length / 2)] ?? 1;
  return <MetricChart title={title} series={data} unit={pace ? "мин/км" : channels[0].unit} yKind={pace ? "pace" : "number"} xKind="duration" maxGap={typical * 3} zero={!pace && channels[0].key === "speed_kmh"} />;
}
