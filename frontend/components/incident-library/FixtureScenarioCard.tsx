"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { replayFixture } from "@/lib/api";
import type { FixtureScenario } from "@/lib/types";
import { CategoryDot, SeverityBadge } from "@/components/ui";

export function FixtureScenarioCard({ fixture }: { fixture: FixtureScenario }) {
  const router = useRouter();
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function launch() {
    setLaunching(true);
    setError(null);
    try {
      const res = await replayFixture(fixture.fixture_id);
      router.push(`/investigations/${res.investigation_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start replay");
      setLaunching(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4 hover:border-border-strong transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <CategoryDot category={fixture.ground_truth.root_cause_category} />
          <span className="font-mono text-[11px] text-fg-faint">{fixture.fixture_id}</span>
        </div>
        <SeverityBadge severity={fixture.incident.severity} />
      </div>

      <div>
        <h3 className="font-medium text-fg text-[13px]">{fixture.incident.alert_name}</h3>
        <p className="mt-1 text-[12px] text-fg-muted leading-relaxed line-clamp-3">
          {fixture.description || fixture.incident.description}
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px] text-fg-muted border-t border-border pt-2">
        <dt className="text-fg-faint">Service</dt>
        <dd className="text-right">{fixture.incident.service_name}</dd>
        <dt className="text-fg-faint">Root cause</dt>
        <dd className="text-right capitalize">{fixture.ground_truth.root_cause_category}</dd>
        <dt className="text-fg-faint">Expected authority</dt>
        <dd className="text-right">{fixture.ground_truth.expected_authority_level}</dd>
      </dl>

      {error && <p className="text-[11px] text-danger">{error}</p>}

      <button
        onClick={launch}
        disabled={launching}
        className="mt-1 rounded bg-accent px-3 py-1.5 text-[12px] font-medium text-white hover:bg-accent/90 disabled:opacity-50 transition-colors"
      >
        {launching ? "Starting…" : "Start investigation"}
      </button>
    </div>
  );
}
