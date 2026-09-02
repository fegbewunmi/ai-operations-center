import type { AuthorityLevel, InvestigationPhase, RootCauseCategory } from "@/lib/types";

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col rounded-md border border-border bg-surface ${className}`}>
      {title && (
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-fg-muted">{title}</h2>
          {action}
        </div>
      )}
      <div className="flex-1 min-h-0 flex flex-col">{children}</div>
    </div>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  P1: "bg-danger/15 text-danger border-danger/30",
  P2: "bg-warn/15 text-warn border-warn/30",
  P3: "bg-fg-faint/15 text-fg-muted border-border-strong",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const cls = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.P3;
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>
      {severity}
    </span>
  );
}

const PHASE_STYLES: Record<InvestigationPhase, { label: string; cls: string }> = {
  planning: { label: "Planning", cls: "bg-accent/15 text-accent border-accent/30" },
  investigating: { label: "Investigating", cls: "bg-accent/15 text-accent border-accent/30" },
  synthesizing: { label: "Synthesizing", cls: "bg-accent/15 text-accent border-accent/30" },
  responding: { label: "Responding", cls: "bg-warn/15 text-warn border-warn/30" },
  complete: { label: "Complete", cls: "bg-ok/15 text-ok border-ok/30" },
  escalated: { label: "Escalated", cls: "bg-danger/15 text-danger border-danger/30" },
};

export function PhasePill({ phase }: { phase: InvestigationPhase }) {
  const style = PHASE_STYLES[phase] ?? PHASE_STYLES.planning;
  const pulsing = phase !== "complete" && phase !== "escalated";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium ${style.cls}`}>
      {pulsing && <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse" />}
      {style.label}
    </span>
  );
}

export function AuthorityBadge({ level }: { level: AuthorityLevel }) {
  const cls =
    level === "L3"
      ? "bg-danger/15 text-danger border-danger/30"
      : level === "L2"
        ? "bg-warn/15 text-warn border-warn/30"
        : "bg-ok/15 text-ok border-ok/30";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>
      {level}
    </span>
  );
}

export const CATEGORY_COLOR: Record<RootCauseCategory, string> = {
  deployment: "var(--cat-deployment)",
  dependency: "var(--cat-dependency)",
  resource: "var(--cat-resource)",
  configuration: "var(--cat-configuration)",
  infrastructure: "var(--cat-infrastructure)",
  unknown: "var(--cat-unknown)",
};

export function CategoryDot({ category }: { category: RootCauseCategory }) {
  return (
    <span
      className="inline-block h-2 w-2 rounded-full shrink-0"
      style={{ backgroundColor: CATEGORY_COLOR[category] }}
      title={category}
    />
  );
}

export function ConfidenceBar({ pct }: { pct: number }) {
  const color = pct >= 75 ? "var(--ok)" : pct >= 40 ? "var(--warn)" : "var(--danger)";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-surface-hover overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.max(0, Math.min(100, pct))}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-[11px] tabular-nums text-fg-muted w-9 text-right">{pct.toFixed(0)}%</span>
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center px-4 text-center text-[12px] text-fg-faint">
      {children}
    </div>
  );
}

export function formatRelativeTime(iso: string | null): string {
  if (!iso) return "-";
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return date.toLocaleDateString();
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return `${min}m ${sec}s`;
}
