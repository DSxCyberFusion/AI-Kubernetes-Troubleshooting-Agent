"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useAuth } from "@/components/auth-provider";
import { getInsforgeClient } from "@/lib/insforge";
import {
  createInvestigationRecord,
  getAffectedNamespaceLabel,
  getAffectedNamespaces,
  getRequestErrorMessage,
  investigateCluster,
  initializeInvestigationProgress,
  loadKubernetesContexts,
  loadInvestigationProgress,
  loadRecentInvestigations,
  updateInvestigationProgress,
  type InvestigationResponse,
  type InvestigationProgressUpdate,
  type InvestigationProgressRecord,
  type KubernetesContext,
  updateInvestigationRecord,
  type InvestigationRecord,
} from "@/services/investigations";

function LoginForm() {
  const { signIn, signInWithGoogle, authError } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [resetCode, setResetCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [resetCodeSent, setResetCodeSent] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    const signInError = await signIn(email, password);
    if (signInError) setError(signInError);
    setSubmitting(false);
  }

  async function handleSendResetCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const { data, error: resetError } = await getInsforgeClient().auth.sendResetPasswordEmail({ email });
      if (resetError) {
        setError(resetError.message);
      } else {
        setResetCodeSent(true);
        setNotice(data?.message ?? "If an account exists for this email, a reset code has been sent.");
      }
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Could not send a password reset code.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const client = getInsforgeClient();
      const { data, error: codeError } = await client.auth.exchangeResetPasswordToken({ email, code: resetCode });
      if (codeError) {
        setError(codeError.message);
        return;
      }
      if (!data?.token) {
        setError("The reset code could not be verified. Request a new code and try again.");
        return;
      }

      const { error: passwordError } = await client.auth.resetPassword({
        newPassword,
        otp: data.token,
      });
      if (passwordError) {
        setError(passwordError.message);
        return;
      }

      setResetMode(false);
      setResetCodeSent(false);
      setResetCode("");
      setPassword("");
      setNotice("Password updated. Sign in with your email and new password.");
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Could not update your password.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleGoogleSignIn() {
    setSubmitting(true);
    setError(null);
    setNotice(null);
    const signInError = await signInWithGoogle();
    if (signInError) setError(signInError);
    setSubmitting(false);
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand-mark" aria-hidden="true">K</div>
        <p className="eyebrow">Cluster operations</p>
        <h1>AI Kubernetes Agent</h1>
        <p className="muted-copy">Sign in to investigate your cluster and review diagnoses.</p>
        {resetMode ? (
          <>
            <form
              className="login-form"
              onSubmit={resetCodeSent ? handleResetPassword : handleSendResetCode}
            >
              <label htmlFor="email">Email</label>
              <input
                autoComplete="email"
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                readOnly={resetCodeSent}
                required
                type="email"
                value={email}
              />
              {resetCodeSent && (
                <>
                  <label htmlFor="reset-code">Reset code</label>
                  <input
                    autoComplete="one-time-code"
                    id="reset-code"
                    onChange={(event) => setResetCode(event.target.value)}
                    required
                    value={resetCode}
                  />
                  <label htmlFor="new-password">New password</label>
                  <input
                    autoComplete="new-password"
                    id="new-password"
                    minLength={6}
                    onChange={(event) => setNewPassword(event.target.value)}
                    required
                    type="password"
                    value={newPassword}
                  />
                </>
              )}
              {error && <p className="error-message" role="alert">{error}</p>}
              {notice && <p className="success-message" role="status">{notice}</p>}
              <button className="primary-button full-width" disabled={submitting} type="submit">
                {submitting
                  ? "Please wait..."
                  : resetCodeSent
                    ? "Update password"
                    : "Send reset code"}
              </button>
            </form>
            <button
              className="auth-link"
              onClick={() => {
                setResetMode(false);
                setResetCodeSent(false);
                setError(null);
                setNotice(null);
              }}
              type="button"
            >
              Back to sign in
            </button>
          </>
        ) : (
          <>
            <form className="login-form" onSubmit={handleSubmit}>
              <label htmlFor="email">Email</label>
              <input
                autoComplete="email"
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
              <label htmlFor="password">Password</label>
              <input
                autoComplete="current-password"
                id="password"
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
              {(error || authError) && <p className="error-message" role="alert">{error ?? authError}</p>}
              {notice && <p className="success-message" role="status">{notice}</p>}
              <button className="primary-button full-width" disabled={submitting} type="submit">
                {submitting ? "Signing in..." : "Sign in"}
              </button>
            </form>
            <button
              className="auth-link"
              onClick={() => {
                setResetMode(true);
                setError(null);
                setNotice(null);
              }}
              type="button"
            >
              Forgot password or need to set one?
            </button>
            <button
              className="google-button full-width"
              disabled={submitting}
              onClick={() => void handleGoogleSignIn()}
              type="button"
            >
              Continue with Google
            </button>
          </>
        )}
        <p className="auth-footnote">Your account is managed by your InsForge project.</p>
      </section>
    </main>
  );
}

function statusLabel(status: InvestigationRecord["status"]): string {
  if (status === "success") return "Complete";
  if (status === "partial") return "Partial";
  if (status === "failed") return "Failed";
  return "Running";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function HomePage() {
  const { user, loading, configurationError, signOut, getAccessToken } = useAuth();
  const [history, setHistory] = useState<InvestigationRecord[]>([]);
  const [clusters, setClusters] = useState<KubernetesContext[]>([]);
  const [selectedContext, setSelectedContext] = useState("");
  const [clustersLoading, setClustersLoading] = useState(true);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [selected, setSelected] = useState<InvestigationRecord | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [investigating, setInvestigating] = useState(false);
  const [progress, setProgress] = useState<InvestigationProgressRecord[]>([]);
  const [realtimeConnected, setRealtimeConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;

    let active = true;
    setClustersLoading(true);
    setClusterError(null);
    void (async () => {
      try {
        const accessToken = await getAccessToken();
        const result = await loadKubernetesContexts(accessToken);
        if (!active) return;
        setClusters(result.contexts);
        setSelectedContext((current) => {
          if (result.contexts.some((context) => context.name === current)) return current;
          return result.current_context ?? result.contexts[0]?.name ?? "";
        });
      } catch (clusterLoadError) {
        if (active) setClusterError(getRequestErrorMessage(clusterLoadError));
      } finally {
        if (active) setClustersLoading(false);
      }
    })();

    return () => {
      active = false;
    };
  }, [getAccessToken, user]);

  useEffect(() => {
    if (!user) return;

    let active = true;
    let client: ReturnType<typeof getInsforgeClient>;
    try {
      client = getInsforgeClient();
    } catch (clientError) {
      setError(getRequestErrorMessage(clientError));
      return;
    }

    const channel = `investigation:${user.id}`;

    const refreshHistory = async () => {
      const recent = await loadRecentInvestigations(client);
      if (!active) return;
      setHistory(recent);
      setSelected((current) => {
        if (current) return recent.find((record) => record.id === current.id) ?? current;
        return recent[0] ?? null;
      });
    };

    const handleRealtimeUpdate = () => {
      void refreshHistory().catch((refreshError: unknown) => {
        if (active) setError(getRequestErrorMessage(refreshError));
      });
    };

    const handleProgressUpdate = (payload: {
      investigation_id?: string;
      sequence?: number;
      step?: string;
      status?: InvestigationProgressRecord["status"];
    }) => {
      if (!payload.investigation_id) return;
      void loadInvestigationProgress(client, payload.investigation_id)
        .then((rows) => {
          if (active) setProgress(rows);
        })
        .catch((refreshError: unknown) => {
          if (active) setError(getRequestErrorMessage(refreshError));
        });
    };

    client.realtime.on("investigation_updated", handleRealtimeUpdate);
    client.realtime.on("investigation_progress_updated", handleProgressUpdate);
    void (async () => {
      try {
        const subscription = await client.realtime.subscribe(channel);
        if (!subscription.ok) {
          throw new Error("Could not subscribe to live investigation updates.");
        }
        if (!active) return;
        setRealtimeConnected(true);
        await refreshHistory();
      } catch (realtimeError) {
        if (active) {
          setRealtimeConnected(false);
          setError(getRequestErrorMessage(realtimeError));
        }
      }
    })();

    return () => {
      active = false;
      client.realtime.off("investigation_updated", handleRealtimeUpdate);
      client.realtime.off("investigation_progress_updated", handleProgressUpdate);
      client.realtime.unsubscribe(channel);
      client.realtime.disconnect();
    };
  }, [user]);

  useEffect(() => {
    if (!selected?.id) return;
    const client = getInsforgeClient();
    void loadInvestigationProgress(client, selected.id)
      .then(setProgress)
      .catch((progressError: unknown) => {
        setError(getRequestErrorMessage(progressError));
      });
  }, [selected?.id]);

  async function handleInvestigate() {
    if (!selectedContext) {
      setError("Select an available Kubernetes cluster before starting an investigation.");
      return;
    }
    setError(null);
    setInvestigating(true);
    setActiveId(null);
    setProgress([]);

    let runningRecord: InvestigationRecord;
    try {
      const client = getInsforgeClient();
      runningRecord = await createInvestigationRecord(client, selectedContext);
      setActiveId(runningRecord.id);
      setSelected(runningRecord);
      setHistory((current) => [runningRecord, ...current.filter((item) => item.id !== runningRecord.id)].slice(0, 10));
      const progressRows = await initializeInvestigationProgress(client, runningRecord.id);
      setProgress(progressRows);
    } catch (createError) {
      setError(`Could not start the investigation: ${getRequestErrorMessage(createError)}`);
      setInvestigating(false);
      return;
    }

    const progressClient = getInsforgeClient();
    let activeSequence = 1;
    const completedSequences = new Set<number>();
    const saveProgress = async (update: InvestigationProgressUpdate) => {
      if (update.status === "running") activeSequence = update.sequence;
      if (update.status === "complete") completedSequences.add(update.sequence);
      setProgress((current) => current.map((item) =>
        item.sequence === update.sequence
          ? {
            ...item,
            status: update.status,
            message: update.message ?? null,
          }
          : item,
      ));
      try {
        await updateInvestigationProgress(
          progressClient,
          runningRecord.id,
          update.sequence,
          update.status,
          update.message,
        );
      } catch (progressError) {
        setError(`Investigation is still running, but live progress could not be saved: ${getRequestErrorMessage(progressError)}`);
      }
    };

    let response: InvestigationResponse;
    try {
      const accessToken = await getAccessToken();
      response = await investigateCluster(accessToken, selectedContext, saveProgress);
    } catch (requestError) {
      const message = getRequestErrorMessage(requestError);
      let progressErrorMessage: string | null = null;
      for (let sequence = 1; sequence <= 6; sequence += 1) {
        if (completedSequences.has(sequence)) continue;
        try {
          await updateInvestigationProgress(
            progressClient,
            runningRecord.id,
            sequence,
            "failed",
            sequence === activeSequence ? message : "Investigation stopped before this stage",
          );
        } catch (progressError) {
          progressErrorMessage = getRequestErrorMessage(progressError);
          break;
        }
      }
      try {
        setProgress(await loadInvestigationProgress(progressClient, runningRecord.id));
      } catch (progressError) {
        progressErrorMessage ??= getRequestErrorMessage(progressError);
      }
      setSelected({
        ...runningRecord,
        status: "failed",
        error_message: message,
      });
      setHistory((current) => current.map((record) =>
        record.id === runningRecord.id
          ? { ...record, status: "failed", error_message: message }
          : record,
      ));
      let historyError: string | null = null;
      try {
        const failed = await updateInvestigationRecord(getInsforgeClient(), runningRecord.id, {
          status: "failed",
          error_message: message,
        });
        setSelected(failed);
        setHistory((current) => [failed, ...current.filter((record) => record.id !== failed.id)].slice(0, 10));
      } catch (saveError) {
        historyError = getRequestErrorMessage(saveError);
      }
      const additionalErrors = [
        historyError ? `History could not be updated: ${historyError}` : null,
        progressErrorMessage ? `Live progress could not be updated: ${progressErrorMessage}` : null,
      ].filter((item): item is string => item !== null);
      setError(additionalErrors.length > 0 ? `${message} ${additionalErrors.join(" ")}` : message);
      setInvestigating(false);
      return;
    }

    try {
      setProgress(await loadInvestigationProgress(progressClient, runningRecord.id));
    } catch (progressError) {
      setError(getRequestErrorMessage(progressError));
    }

    const affectedNamespaces = getAffectedNamespaces(response.investigation);
    const completedRecord: InvestigationRecord = {
      ...runningRecord,
      namespace: affectedNamespaces.length === 1 ? affectedNamespaces[0] : "",
      affected_namespaces: affectedNamespaces,
      historical_warnings: response.investigation.pods?.historical_warnings ?? [],
      status: response.status,
      root_cause: response.diagnosis?.root_cause ?? null,
      explanation: response.diagnosis?.explanation ?? null,
      fix: response.diagnosis?.fix ?? null,
      kubectl_commands: response.diagnosis?.kubectl_commands ?? [],
      confidence: response.diagnosis?.confidence ?? null,
      error_message: response.error ?? null,
    };
    setSelected(completedRecord);

    try {
      const saved = await updateInvestigationRecord(getInsforgeClient(), runningRecord.id, {
        namespace: completedRecord.namespace,
        affected_namespaces: completedRecord.affected_namespaces,
        historical_warnings: completedRecord.historical_warnings,
        status: completedRecord.status,
        root_cause: completedRecord.root_cause,
        explanation: completedRecord.explanation,
        fix: completedRecord.fix,
        kubectl_commands: completedRecord.kubectl_commands,
        confidence: completedRecord.confidence,
        error_message: completedRecord.error_message,
      });
      setSelected(saved);
      setHistory((current) => [saved, ...current.filter((item) => item.id !== saved.id)].slice(0, 10));
    } catch (saveError) {
      setError(`Diagnosis received, but history could not be saved: ${getRequestErrorMessage(saveError)}`);
    } finally {
      setInvestigating(false);
    }
  }

  async function handleSignOut() {
    const signOutError = await signOut();
    if (signOutError) setError(signOutError);
  }

  if (loading) {
    return (
      <main className="auth-page">
        <p className="muted-copy">Checking your session...</p>
      </main>
    );
  }

  if (configurationError) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <p className="eyebrow">Setup required</p>
          <h1>Connect InsForge</h1>
          <p className="error-message" role="alert">{configurationError}</p>
          <p className="muted-copy">Add the InsForge URL and anon key to the frontend environment, then restart the app.</p>
        </section>
      </main>
    );
  }

  if (!user) return <LoginForm />;

  const isSelectedRun = investigating && selected?.id === activeId;

  return (
    <main className="dashboard-page">
      <header className="topbar">
        <a className="brand" href="/" aria-label="AI Kubernetes Agent home">
          <span className="brand-mark" aria-hidden="true">K</span>
          <span>AI Kubernetes Agent</span>
        </a>
        <div className="topbar-actions">
          <span className={`connection-status ${realtimeConnected ? "connected" : ""}`}>
            <span className="status-dot" aria-hidden="true" />
            {realtimeConnected ? "Live updates on" : "Connecting"}
          </span>
          <span className="user-email">{user.email}</span>
          <button className="quiet-button" onClick={() => void handleSignOut()} type="button">
            Sign out
          </button>
        </div>
      </header>

      <div className="dashboard-content">
        <section className="welcome-row">
          <div>
            <p className="eyebrow">Cluster operations</p>
            <h1>Investigate your cluster</h1>
            <p className="muted-copy">Find the cause of Kubernetes issues and get a practical next step.</p>
            <div className="cluster-picker">
              <span className="field-label">Select a Kubernetes cluster</span>
              {clustersLoading ? (
                <p className="muted-copy">Loading kubeconfig clusters...</p>
              ) : clusterError ? (
                <p className="error-message" role="alert">{clusterError}</p>
              ) : clusters.length === 0 ? (
                <p className="muted-copy">No Kubernetes contexts were found in the backend kubeconfig.</p>
              ) : (
                <div className="cluster-options" role="group" aria-label="Kubernetes clusters">
                  {clusters.map((cluster) => (
                    <button
                      aria-pressed={selectedContext === cluster.name}
                      className={`cluster-option ${selectedContext === cluster.name ? "selected" : ""}`}
                      disabled={investigating}
                      key={cluster.name}
                      onClick={() => setSelectedContext(cluster.name)}
                      type="button"
                    >
                      <strong>{cluster.cluster || cluster.name}</strong>
                      <span>{cluster.name}{cluster.current ? " · current context" : ""}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <button
            className="primary-button"
            disabled={investigating || clustersLoading || !selectedContext}
            onClick={() => void handleInvestigate()}
            type="button"
          >
            {investigating ? "Investigating..." : "Investigate Cluster"}
          </button>
        </section>

        {error && <div className="notice error-notice" role="alert">{error}</div>}

        <section className="panel progress-panel" aria-live="polite">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Live status</p>
              <h2>Investigation progress</h2>
            </div>
            {investigating && <span className="status-pill running">In progress</span>}
          </div>
          {investigating ? (
            progress.length > 0 ? (
              <div className="progress-list" aria-label="Investigation stages">
                {progress.map((item) => (
                  <div className="progress-item" key={item.id}>
                    <span className={`progress-status ${item.status}`} aria-hidden="true">
                      {item.status === "complete" ? "✓" : item.status === "failed" ? "!" : item.status === "running" ? "•" : ""}
                    </span>
                    <div>
                      <strong>{item.step}</strong>
                      {item.message && <p className="muted-copy">{item.message}</p>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="progress-state">
                <span className="spinner" aria-hidden="true" />
                <div>
                  <strong>Investigating Kubernetes Cluster...</strong>
                  <p className="muted-copy">Preparing the investigation progress record.</p>
                </div>
              </div>
            )
          ) : progress.length > 0 ? (
            <div className="progress-list" aria-label="Investigation stages">
              {progress.map((item) => (
                <div className="progress-item" key={item.id}>
                  <span className={`progress-status ${item.status}`} aria-hidden="true">
                    {item.status === "complete" ? "✓" : item.status === "failed" ? "!" : item.status === "running" ? "•" : ""}
                  </span>
                  <div>
                    <strong>{item.step}</strong>
                    {item.message && <p className="muted-copy">{item.message}</p>}
                  </div>
                </div>
              ))}
            </div>
          ) : selected ? (
            <div className="progress-state">
              <span className={`status-icon ${selected.status === "failed" ? "failed" : "complete"}`} aria-hidden="true">
                {selected.status === "failed" ? "!" : "✓"}
              </span>
              <div>
                <strong>{statusLabel(selected.status)}</strong>
                <p className="muted-copy">
                  {selected.status === "failed"
                    ? selected.error_message ?? "The investigation did not complete."
                    : `Affected namespaces: ${getAffectedNamespaceLabel(selected)}.`}
                </p>
              </div>
            </div>
          ) : (
            <p className="muted-copy">Start an investigation to see its live status and diagnosis here.</p>
          )}
        </section>

        <section className="panel diagnosis-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Result</p>
              <h2>Diagnosis</h2>
            </div>
            {selected && <span className={`status-pill ${selected.status}`}>{statusLabel(selected.status)}</span>}
          </div>

          {isSelectedRun ? (
            <div className="skeleton-lines" aria-label="Waiting for diagnosis">
              <span />
              <span />
              <span />
            </div>
          ) : selected?.root_cause ? (
            <div className="diagnosis-content">
              <div className="root-cause">
                <span className="field-label">Root cause</span>
                <h3>{selected.root_cause}</h3>
                <p className="muted-copy">{selected.explanation}</p>
              </div>
              <div className="diagnosis-detail">
                <span className="field-label">Suggested fix</span>
                <p>{selected.fix}</p>
              </div>
              {selected.kubectl_commands.length > 0 && (
                <div className="command-block">
                  <span className="field-label">Suggested kubectl command</span>
                  {selected.kubectl_commands.map((command) => (
                    <code key={command}>{command}</code>
                  ))}
                </div>
              )}
              <div className="diagnosis-meta">
                <span><span className="field-label">Kubernetes context</span>{selected.cluster_context ?? "—"}</span>
                <span><span className="field-label">Affected namespace(s)</span>{getAffectedNamespaceLabel(selected)}</span>
                <span><span className="field-label">Confidence</span>{selected.confidence ?? "—"}{selected.confidence === null ? "" : "%"}</span>
              </div>
            </div>
          ) : selected?.status === "partial" ? (
            <div className="empty-diagnosis">
              <strong>Evidence collected, but no diagnosis is available.</strong>
              <p className="muted-copy">{selected.error_message ?? "Try again when the AI service is available."}</p>
            </div>
          ) : selected?.status === "failed" ? (
            <p className="muted-copy">A diagnosis could not be generated for this investigation.</p>
          ) : (
            <p className="muted-copy">Your diagnosis will appear here when the investigation is complete.</p>
          )}
          {selected?.historical_warnings.length ? (
            <div className="diagnosis-detail">
              <span className="field-label">Historical restart warnings</span>
              {selected.historical_warnings.map((warning) => (
                <p key={`${warning.namespace}/${warning.pod}/${warning.container ?? "container"}`}>
                  {warning.namespace}/{warning.pod}
                  {warning.container ? ` (${warning.container})` : ""}: {warning.restart_count} restarts.
                  {warning.previous_termination
                    ? ` Previous termination: ${warning.previous_termination.reason ?? "unknown"}`
                      + (warning.previous_termination.exit_code === null
                        ? ""
                        : ` (exit code ${warning.previous_termination.exit_code})`)
                      + (warning.previous_termination.finished_at
                        ? ` at ${warning.previous_termination.finished_at}`
                        : "")
                    : " Previous termination details unavailable."}
                </p>
              ))}
            </div>
          ) : null}
        </section>

        <section className="panel history-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Your cluster activity</p>
              <h2>Recent investigations</h2>
            </div>
          </div>
          {history.length === 0 ? (
            <p className="muted-copy">No investigations yet. Your recent results will be saved here.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Kubernetes context</th>
                    <th scope="col">Root cause</th>
                    <th scope="col">Affected namespace(s)</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((record) => (
                    <tr className="history-row" key={record.id}>
                      <td>
                        <button
                          aria-pressed={selected?.id === record.id}
                          className="history-select-button"
                          onClick={() => setSelected(record)}
                          type="button"
                        >
                          {formatDate(record.created_at)}
                        </button>
                      </td>
                      <td>{record.cluster_context ?? "—"}</td>
                      <td>{record.root_cause ?? (record.status === "running" ? "Investigating..." : "—")}</td>
                      <td>
                        {getAffectedNamespaceLabel(record)}
                        {record.historical_warnings.length > 0 && (
                          <p className="muted-copy">
                            Historical warnings: {[...new Set(record.historical_warnings.map((warning) => warning.namespace))].join(", ")}
                          </p>
                        )}
                      </td>
                      <td>{record.confidence === null ? "—" : `${record.confidence}%`}</td>
                      <td><span className={`status-pill ${record.status}`}>{statusLabel(record.status)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <footer className="dashboard-footer">
          Live status and investigation history are secured to your InsForge account.
        </footer>
      </div>
    </main>
  );
}
