"use client";

import Link from "next/link";
import type { InvestigationListItem } from "@/lib/types";
import { PhasePill, SeverityBadge, EmptyState, formatRelativeTime } from "@/components/ui";

export function InvestigationHistoryTable({ items }: { items: InvestigationListItem[] }) {
  if (items.length === 0) {
    return (
      <EmptyState>
        No investigations yet. Start one from a fixture scenario above, or trigger one via
        <span className="mono ml-1">POST /v1/investigations</span>.
      </EmptyState>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[12px]">
        <thead>
          <tr className="border-b border-border text-left text-[11px] text-fg-faint">
            <th className="px-3 py-2 font-medium">Alert</th>
            <th className="px-3 py-2 font-medium">Service</th>
            <th className="px-3 py-2 font-medium">Severity</th>
            <th className="px-3 py-2 font-medium">Source</th>
            <th className="px-3 py-2 font-medium">Phase</th>
            <th className="px-3 py-2 font-medium">Started</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.investigation_id} className="border-b border-border last:border-0 hover:bg-surface-hover">
              <td className="px-3 py-2">
                <Link href={`/investigations/${item.investigation_id}`} className="text-fg hover:text-accent">
                  {item.alert_name}
                </Link>
              </td>
              <td className="px-3 py-2 text-fg-muted">{item.service_name}</td>
              <td className="px-3 py-2">
                <SeverityBadge severity={item.severity} />
              </td>
              <td className="px-3 py-2 text-fg-muted">
                {item.source === "replay" ? (
                  <span className="mono text-[11px]">{item.fixture_id ?? "replay"}</span>
                ) : (
                  "live"
                )}
              </td>
              <td className="px-3 py-2">
                <PhasePill phase={item.phase} />
              </td>
              <td className="px-3 py-2 text-fg-muted">{formatRelativeTime(item.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
