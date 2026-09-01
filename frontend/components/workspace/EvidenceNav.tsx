"use client";

import type { InvestigationEvidenceResponse } from "@/lib/types";
import { EmptyState, Panel } from "@/components/ui";

const SPECIALIST_META = {
  telemetry: { label: "Telemetry", icon: "◈" },
  deployment: { label: "Deployments", icon: "▲" },
  knowledge: { label: "Knowledge", icon: "▤" },
} as const;

export function EvidenceNav({
  evidence,
  selectedId,
  onSelect,
}: {
  evidence: InvestigationEvidenceResponse | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const groups: { key: keyof typeof SPECIALIST_META; ids: string[]; sublabels: string[] }[] = [
    {
      key: "telemetry",
      ids: (evidence?.telemetry_findings ?? []).map((_, i) => `telemetry-${i}`),
      sublabels: (evidence?.telemetry_findings ?? []).map((f) => f.service),
    },
    {
      key: "deployment",
      ids: (evidence?.deployment_findings ?? []).map((_, i) => `deployment-${i}`),
      sublabels: (evidence?.deployment_findings ?? []).map((f) => f.service),
    },
    {
      key: "knowledge",
      ids: (evidence?.knowledge_context ?? []).map((_, i) => `knowledge-${i}`),
      sublabels: (evidence?.knowledge_context ?? []).map((k) => k.query.slice(0, 24)),
    },
  ];

  const isEmpty = groups.every((g) => g.ids.length === 0);

  return (
    <Panel title="Evidence" className="flex-1 min-h-0">
      <div className="h-full overflow-auto p-1.5">
        {isEmpty ? (
          <EmptyState>No evidence gathered yet.</EmptyState>
        ) : (
          groups.map(
            (g) =>
              g.ids.length > 0 && (
                <div key={g.key} className="mb-2">
                  <div className="px-1.5 py-1 text-[10px] uppercase tracking-wide text-fg-faint">
                    {SPECIALIST_META[g.key].icon} {SPECIALIST_META[g.key].label}
                  </div>
                  {g.ids.map((id, i) => (
                    <button
                      key={id}
                      onClick={() => onSelect(id)}
                      className={`block w-full truncate rounded px-2 py-1 text-left text-[11px] transition-colors ${
                        selectedId === id ? "bg-accent/15 text-accent" : "text-fg-muted hover:bg-surface-hover hover:text-fg"
                      }`}
                    >
                      {g.sublabels[i]}
                    </button>
                  ))}
                </div>
              ),
          )
        )}
      </div>
    </Panel>
  );
}
