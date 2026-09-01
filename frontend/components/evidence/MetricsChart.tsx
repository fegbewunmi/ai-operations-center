"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DeploymentRecord, MetricPoint } from "@/lib/types";
import { EmptyState } from "@/components/ui";

function groupByMetric(points: MetricPoint[]): Map<string, MetricPoint[]> {
  const map = new Map<string, MetricPoint[]>();
  for (const p of points) {
    const arr = map.get(p.name) ?? [];
    arr.push(p);
    map.set(p.name, arr);
  }
  for (const arr of map.values()) {
    arr.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  }
  return map;
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function MetricsChart({
  metrics,
  nearbyDeployments = [],
}: {
  metrics: MetricPoint[];
  nearbyDeployments?: DeploymentRecord[];
}) {
  if (metrics.length === 0) {
    return <EmptyState>No metrics on this finding.</EmptyState>;
  }

  const grouped = groupByMetric(metrics);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {[...grouped.entries()].map(([name, points]) => {
        const baseline = points.find((p) => p.baseline_value !== null)?.baseline_value ?? null;
        const anomalies = points.filter((p) => p.is_anomalous);
        const data = points.map((p) => ({
          t: new Date(p.timestamp).getTime(),
          label: timeLabel(p.timestamp),
          value: p.value,
        }));

        return (
          <div key={name} className="rounded-md border border-border bg-surface-raised p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] font-medium text-fg">{name}</span>
              <span className="text-[10px] text-fg-faint">{points[0]?.unit}</span>
            </div>
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 9, fill: "var(--fg-faint)" }} tickLine={false} axisLine={{ stroke: "var(--border)" }} />
                <YAxis tick={{ fontSize: 9, fill: "var(--fg-faint)" }} tickLine={false} axisLine={false} width={36} />
                <Tooltip
                  contentStyle={{
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    fontSize: 11,
                  }}
                  labelStyle={{ color: "var(--fg-muted)" }}
                />
                {baseline !== null && (
                  <ReferenceLine y={baseline} stroke="var(--fg-faint)" strokeDasharray="3 3" ifOverflow="extendDomain" />
                )}
                {nearbyDeployments.map((d) => (
                  <ReferenceLine
                    key={d.deployment_id}
                    x={timeLabel(d.deployed_at)}
                    stroke="var(--accent)"
                    strokeDasharray="2 2"
                    label={{ value: d.version_to, position: "insideTopLeft", fontSize: 9, fill: "var(--accent)" }}
                  />
                ))}
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={anomalies.length > 0 ? "var(--danger)" : "var(--accent)"}
                  strokeWidth={1.5}
                  dot={{ r: 2 }}
                  activeDot={{ r: 3 }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
            {anomalies.length > 0 && (
              <p className="mt-1 text-[10px] text-danger">{anomalies.length} anomalous point(s)</p>
            )}
          </div>
        );
      })}
    </div>
  );
}
