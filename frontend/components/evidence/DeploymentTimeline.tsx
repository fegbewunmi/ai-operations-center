"use client";

import type { DeploymentRecord } from "@/lib/types";
import { EmptyState } from "@/components/ui";

export function DeploymentTimeline({ deployments }: { deployments: DeploymentRecord[] }) {
  if (deployments.length === 0) {
    return <EmptyState>No deployments found in the search window.</EmptyState>;
  }

  return (
    <div className="flex flex-col gap-2">
      {deployments.map((d) => {
        const near = d.minutes_before_onset !== null && d.minutes_before_onset >= 0 && d.minutes_before_onset <= 30;
        return (
          <div
            key={d.deployment_id}
            className={`rounded-md border p-2.5 ${near ? "border-warn/50 bg-warn/5" : "border-border bg-surface-raised"}`}
          >
            <div className="flex items-center justify-between">
              <span className="mono text-[12px] font-medium text-fg">
                {d.version_from ?? "?"} → {d.version_to}
              </span>
              <StatusPill status={d.status} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-fg-muted">
              <span>{new Date(d.deployed_at).toLocaleString()}</span>
              <span>by {d.deployed_by}</span>
              {d.minutes_before_onset !== null && (
                <span className={near ? "font-semibold text-warn" : ""}>
                  {d.minutes_before_onset >= 0
                    ? `${d.minutes_before_onset.toFixed(0)}m before onset`
                    : `${Math.abs(d.minutes_before_onset).toFixed(0)}m after onset`}
                </span>
              )}
              {d.git_commit_sha && <span className="mono">{d.git_commit_sha.slice(0, 8)}</span>}
            </div>
            {d.config_changes.length > 0 && (
              <ul className="mt-1.5 list-disc list-inside space-y-0.5">
                {d.config_changes.map((c, i) => (
                  <li key={i} className="text-[11px] text-fg-muted leading-snug">
                    {c}
                  </li>
                ))}
              </ul>
            )}
            {d.rollback_available && (
              <div className="mt-1.5 text-[10px] text-ok">
                Rollback available{d.rollback_target_version ? ` → ${d.rollback_target_version}` : ""}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatusPill({ status }: { status: DeploymentRecord["status"] }) {
  const cls =
    status === "success"
      ? "text-ok"
      : status === "failed"
        ? "text-danger"
        : status === "rolled_back"
          ? "text-warn"
          : "text-fg-muted";
  return <span className={`text-[10px] font-medium uppercase ${cls}`}>{status.replace("_", " ")}</span>;
}
