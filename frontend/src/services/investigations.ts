import type { InsForgeClient } from "@insforge/sdk";

import { apiClient } from "@/services/api";

export type InvestigationStatus = "running" | "success" | "partial" | "failed";

export type Diagnosis = {
  root_cause: string;
  explanation: string;
  fix: string;
  kubectl_commands: string[];
  confidence: number;
};

export type KubernetesContext = {
  name: string;
  cluster: string;
  namespace: string | null;
  current: boolean;
};

export type InvestigationProgressUpdate = {
  sequence: number;
  step: string;
  status: InvestigationProgressRecord["status"];
  message?: string;
};

type NamespacedItem = {
  namespace?: string;
};

export type HistoricalPodWarning = {
  pod: string;
  namespace: string;
  container: string | null;
  restart_count: number;
  previous_termination: {
    reason: string | null;
    exit_code: number | null;
    started_at: string | null;
    finished_at: string | null;
  } | null;
};

export type InvestigationEvidence = {
  pods?: {
    problematic_pods?: NamespacedItem[];
    historical_warnings?: HistoricalPodWarning[];
  };
  events?: { findings?: NamespacedItem[] };
  deployments?: {
    deployments?: (NamespacedItem & { healthy?: boolean })[];
  };
  network?: {
    issues?: NamespacedItem[];
    dns_issues?: NamespacedItem[];
  };
};

export type InvestigationResponse = {
  status: "success" | "partial";
  context_name: string;
  investigation: InvestigationEvidence;
  diagnosis: Diagnosis | null;
  error?: string;
};

export type InvestigationRecord = {
  id: string;
  created_at: string;
  cluster_context: string | null;
  namespace: string;
  affected_namespaces: string[];
  historical_warnings: HistoricalPodWarning[];
  status: InvestigationStatus;
  root_cause: string | null;
  explanation: string | null;
  fix: string | null;
  kubectl_commands: string[];
  confidence: number | null;
  error_message: string | null;
};

export type InvestigationProgressRecord = {
  id: string;
  investigation_id: string;
  sequence: number;
  step: string;
  status: "pending" | "running" | "complete" | "failed";
  message: string | null;
};

const historyColumns =
  "id, created_at, cluster_context, namespace, affected_namespaces, historical_warnings, status, root_cause, explanation, fix, kubectl_commands, confidence, error_message";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isDiagnosis(value: unknown): value is Diagnosis {
  return (
    isRecord(value) &&
    typeof value.root_cause === "string" &&
    typeof value.explanation === "string" &&
    typeof value.fix === "string" &&
    Array.isArray(value.kubectl_commands) &&
    value.kubectl_commands.every((command) => typeof command === "string") &&
    typeof value.confidence === "number"
  );
}

export function isInvestigationResponse(value: unknown): value is InvestigationResponse {
  return (
    isRecord(value) &&
    (value.status === "success" || value.status === "partial") &&
    typeof value.context_name === "string" &&
    isRecord(value.investigation) &&
    (value.diagnosis === null || isDiagnosis(value.diagnosis)) &&
    (value.error === undefined || typeof value.error === "string")
  );
}

export async function loadKubernetesContexts(
  accessToken: string,
): Promise<{ current_context: string | null; contexts: KubernetesContext[] }> {
  const { data } = await apiClient.get<unknown>("/clusters", {
    timeout: 15_000,
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!isRecord(data) || !Array.isArray(data.contexts)) {
    throw new Error("The backend returned an invalid kubeconfig context list.");
  }
  const contexts = data.contexts.map((context) => {
    if (
      !isRecord(context) ||
      typeof context.name !== "string" ||
      typeof context.cluster !== "string" ||
      (context.namespace !== null && typeof context.namespace !== "string") ||
      typeof context.current !== "boolean"
    ) {
      throw new Error("The backend returned an invalid kubeconfig context.");
    }
    return {
      name: context.name,
      cluster: context.cluster,
      namespace: typeof context.namespace === "string" ? context.namespace : null,
      current: context.current,
    };
  });
  return {
    current_context: typeof data.current_context === "string" ? data.current_context : null,
    contexts,
  };
}

export async function investigateCluster(
  accessToken: string,
  contextName: string,
  onProgress: (progress: InvestigationProgressUpdate) => Promise<void>,
): Promise<InvestigationResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 360_000);
  const baseUrl = apiClient.defaults.baseURL ?? "http://localhost:8000";

  try {
    const response = await fetch(new URL("/investigate/stream", baseUrl), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ context_name: contextName }),
      signal: controller.signal,
    });

    if (!response.ok) {
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      const detail = isRecord(payload) ? payload.detail : null;
      throw new Error(
        typeof detail === "string"
          ? detail
          : `The investigation request failed with HTTP ${response.status}.`,
      );
    }

    if (!response.body) {
      throw new Error("The backend did not open an investigation progress stream.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: InvestigationResponse | null = null;

    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";
      if (done && buffer.trim()) {
        events.push(buffer);
        buffer = "";
      }

      for (const eventText of events) {
        const dataLine = eventText
          .split(/\r?\n/)
          .find((line) => line.startsWith("data:"));
        if (!dataLine) continue;

        let event: unknown;
        try {
          event = JSON.parse(dataLine.slice(5).trim());
        } catch {
          throw new Error("The backend sent invalid investigation progress data.");
        }
        if (!isRecord(event) || typeof event.type !== "string") {
          throw new Error("The backend sent an invalid investigation progress event.");
        }
        if (event.type === "progress") {
          const progressStatus = event.status;
          if (
            typeof event.sequence !== "number" ||
            typeof event.step !== "string" ||
            (
              progressStatus !== "pending" &&
              progressStatus !== "running" &&
              progressStatus !== "complete" &&
              progressStatus !== "failed"
            )
          ) {
            throw new Error("The backend sent an invalid investigation progress update.");
          }
          await onProgress({
            sequence: event.sequence,
            step: event.step,
            status: progressStatus,
            ...(typeof event.message === "string" ? { message: event.message } : {}),
          });
        } else if (event.type === "result") {
          if (!isInvestigationResponse(event.response)) {
            throw new Error("The backend returned an invalid investigation response.");
          }
          result = event.response;
        } else if (event.type === "error") {
          throw new Error(
            typeof event.message === "string"
              ? event.message
              : "The investigation could not be completed.",
          );
        }
      }

      if (done) break;
    }

    if (!result) {
      throw new Error("The investigation stream ended before returning a diagnosis.");
    }
    return result;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(
        "The investigation timed out. Check that the backend and selected cluster are reachable, then try again.",
      );
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export async function loadRecentInvestigations(
  client: InsForgeClient,
): Promise<InvestigationRecord[]> {
  const { data, error } = await client.database
    .from("investigations")
    .select(historyColumns)
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(10);

  if (error) throw error;
  return (data ?? []) as InvestigationRecord[];
}

export async function loadInvestigationProgress(
  client: InsForgeClient,
  investigationId: string,
): Promise<InvestigationProgressRecord[]> {
  const { data, error } = await client.database
    .from("investigation_progress")
    .select("id, investigation_id, sequence, step, status, message")
    .eq("investigation_id", investigationId)
    .order("sequence", { ascending: true });

  if (error) throw error;
  return (data ?? []) as InvestigationProgressRecord[];
}

export async function initializeInvestigationProgress(
  client: InsForgeClient,
  investigationId: string,
): Promise<InvestigationProgressRecord[]> {
  const steps = [
    "Checking Pods",
    "Reading Logs",
    "Analyzing Events",
    "Inspecting Deployments",
    "Checking Networking",
    "AI Reasoning",
  ];
  const rows = steps.map((step, index) => ({
    investigation_id: investigationId,
    sequence: index + 1,
    step,
    status: index === 0 ? "running" : "pending",
    message: index === 0 ? "Investigation started" : null,
  }));

  const { data, error } = await client.database
    .from("investigation_progress")
    .insert(rows)
    .select("id, investigation_id, sequence, step, status, message")
    .order("sequence", { ascending: true });

  if (error) throw error;
  return (data ?? []) as InvestigationProgressRecord[];
}

export async function updateInvestigationProgress(
  client: InsForgeClient,
  investigationId: string,
  sequence: number,
  status: InvestigationProgressRecord["status"],
  message?: string,
): Promise<void> {
  const { error } = await client.database
    .from("investigation_progress")
    .update({ status, message: message ?? null })
    .eq("investigation_id", investigationId)
    .eq("sequence", sequence);

  if (error) throw error;
}

export async function createInvestigationRecord(
  client: InsForgeClient,
  contextName: string,
): Promise<InvestigationRecord> {
  const { data, error } = await client.database
    .from("investigations")
    .insert([{
      cluster_context: contextName,
      namespace: "",
      affected_namespaces: [],
      historical_warnings: [],
      status: "running",
    }])
    .select(historyColumns)
    .single();

  if (error) throw error;
  if (!data) throw new Error("InsForge did not return the new investigation record.");
  return data as InvestigationRecord;
}

export async function updateInvestigationRecord(
  client: InsForgeClient,
  id: string,
  updates: Partial<Omit<InvestigationRecord, "id" | "created_at">>,
): Promise<InvestigationRecord> {
  const { data, error } = await client.database
    .from("investigations")
    .update(updates)
    .eq("id", id)
    .select(historyColumns)
    .single();

  if (error) throw error;
  if (!data) throw new Error("InsForge did not return the updated investigation record.");
  return data as InvestigationRecord;
}

export function getAffectedNamespaces(evidence: InvestigationEvidence): string[] {
  const findings: NamespacedItem[] = [
    ...(evidence.pods?.problematic_pods ?? []),
    ...(evidence.events?.findings ?? []),
    ...(evidence.deployments?.deployments ?? [])
      .filter((deployment) => deployment.healthy === false),
    ...(evidence.network?.issues ?? []),
    ...(evidence.network?.dns_issues ?? []),
  ];

  return [...new Set(
    findings
      .map((finding) => finding.namespace?.trim())
      .filter((namespace): namespace is string => Boolean(namespace)),
  )].sort((left, right) => left.localeCompare(right));
}

export function getAffectedNamespaceLabel(record: InvestigationRecord): string {
  if (record.affected_namespaces.length > 0) {
    return record.affected_namespaces.join(", ");
  }
  if (record.namespace.trim()) {
    return `Recorded: ${record.namespace} (legacy; unverified)`;
  }
  return "None identified";
}

export function getRequestErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = error.code;
    if (code === "ECONNABORTED" || code === "ETIMEDOUT") {
      return "The investigation timed out. Check that the backend and cluster are reachable, then try again.";
    }
    if (code === "ERR_NETWORK" || code === "ECONNREFUSED") {
      return "Unable to reach the troubleshooting backend. Verify that it is running and try again.";
    }
  }

  if (error instanceof TypeError && /fetch failed|networkerror/i.test(error.message)) {
    return "Unable to reach the troubleshooting backend. Verify that it is running and try again.";
  }

  if (typeof error === "object" && error !== null && "response" in error) {
    const response = error.response;
    if (isRecord(response) && isRecord(response.data)) {
      const detail = response.data.detail ?? response.data.error;
      if (typeof detail === "string" && detail.length > 0) return detail;
    }
  }

  return error instanceof Error
    ? error.message
    : "An unexpected error occurred while investigating the cluster.";
}
