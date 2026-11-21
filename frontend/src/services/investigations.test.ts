import { describe, expect, it } from "vitest";

import {
  getAffectedNamespaceLabel,
  getAffectedNamespaces,
  type InvestigationEvidence,
  type InvestigationRecord,
} from "./investigations";

function makeRecord(
  overrides: Partial<InvestigationRecord> = {},
): InvestigationRecord {
  return {
    id: "investigation-1",
    created_at: "2026-11-21T00:00:00Z",
    cluster_context: "kind-ai-troubleshooting",
    namespace: "",
    affected_namespaces: [],
    historical_warnings: [],
    status: "success",
    root_cause: null,
    explanation: null,
    fix: null,
    kubectl_commands: [],
    confidence: null,
    error_message: null,
    ...overrides,
  };
}

describe("investigation namespace metadata", () => {
  it("returns the namespace for findings in one namespace", () => {
    const evidence: InvestigationEvidence = {
      pods: { problematic_pods: [{ namespace: "troubleshooting-agent-test" }] },
    };

    expect(getAffectedNamespaces(evidence)).toEqual([
      "troubleshooting-agent-test",
    ]);
  });

  it("deduplicates and sorts namespaces across finding categories", () => {
    const evidence: InvestigationEvidence = {
      pods: { problematic_pods: [{ namespace: "shop" }] },
      events: { findings: [{ namespace: "troubleshooting-agent-test" }] },
      deployments: {
        deployments: [
          { namespace: "shop", healthy: false },
          { namespace: "healthy-namespace", healthy: true },
        ],
      },
      network: {
        issues: [{ namespace: "troubleshooting-agent-test" }],
        dns_issues: [{ namespace: "infra" }],
      },
    };

    expect(getAffectedNamespaces(evidence)).toEqual([
      "infra",
      "shop",
      "troubleshooting-agent-test",
    ]);
  });

  it("returns no namespaces when evidence has no namespaced findings", () => {
    const evidence: InvestigationEvidence = {
      pods: { problematic_pods: [] },
      events: { findings: [] },
      deployments: { deployments: [{ namespace: "healthy-namespace", healthy: true }] },
      network: { issues: [], dns_issues: [] },
    };

    expect(getAffectedNamespaces(evidence)).toEqual([]);
  });

  it("labels existing single-namespace history as legacy and unverified", () => {
    const record = makeRecord({
      namespace: "local-path-storage",
    });

    expect(getAffectedNamespaceLabel(record)).toBe(
      "Recorded: local-path-storage (legacy; unverified)",
    );
  });

  it("displays multiple affected namespaces without choosing one", () => {
    const record = makeRecord({
      namespace: "",
      affected_namespaces: ["shop", "troubleshooting-agent-test"],
    });

    expect(getAffectedNamespaceLabel(record)).toBe(
      "shop, troubleshooting-agent-test",
    );
  });

  it("labels a new record with no namespace findings explicitly", () => {
    const record = makeRecord({
      namespace: "",
      affected_namespaces: [],
    });

    expect(getAffectedNamespaceLabel(record)).toBe("None identified");
  });
});
