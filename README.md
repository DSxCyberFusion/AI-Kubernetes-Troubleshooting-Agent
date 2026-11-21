# AI Kubernetes Troubleshooting Agent

A beginner-friendly monorepo for an on-demand Kubernetes troubleshooting application. It contains a FastAPI backend and a Next.js frontend. The backend collects Kubernetes evidence through read-only `kubectl` commands and uses Gemini to generate an evidence-based diagnosis.

## Requirements

- Docker with Docker Compose
- `kubectl` configured to access the cluster to investigate

## Run with Docker

From the project root:

```bash
docker compose --env-file frontend/.env.local up --build
```

Open:

- Frontend: <http://localhost:3000>
- Backend health: <http://localhost:8000/health>

The backend health endpoint returns:

```json
{
  "status": "healthy",
  "service": "ai-kubernetes-agent"
}
```

### Kubernetes investigation

The dashboard lists the contexts in the backend's kubeconfig and investigates the selected context. Investigations stream collector and AI progress to the dashboard. The backend gathers pod status, relevant failed-pod logs, warning events, deployment health, and service endpoints. It sends evidence to the configured Gemini model when findings are present and returns both the `investigation` and a `diagnosis` containing the root cause, explanation, fix, suggested `kubectl_commands`, prevention recommendation, and confidence score. A healthy cluster returns a no-issues diagnosis without calling the AI service. If Kubernetes collection or AI diagnosis fails, the response reports `partial` status and preserves the evidence collected.

For local development, ensure `kubectl` is installed and the selected contexts have permission to read pods, logs, events, deployments, services, and endpoints. Optionally set `KUBECONFIG_PATH` to the kubeconfig file.

With Docker Compose, `KUBECONFIG_HOST_PATH` selects the host kubeconfig file (defaults to `%USERPROFILE%\\.kube\\config` on Windows). The file is mounted read-only into the backend. The backend image installs `kubectl`; loopback cluster API addresses are routed through Docker's `host.docker.internal` gateway. Do not mount credentials into frontend containers or commit kubeconfig files.

The backend can call Gemini through Google's OpenAI-compatible chat-completions endpoint. For local development, copy `backend/.env.example` to `backend/.env`, set `AI_API_KEY` to a key created in Google AI Studio, and keep the documented Gemini URL/model values. The backend sends the Gemini key only to Google's endpoint. If any `AI_API_*` setting is configured, all three (`AI_API_URL`, `AI_API_KEY`, and `AI_MODEL`) must be configured together; the OpenRouter key is never used as a fallback for another provider. If no `AI_API_*` settings are set, the existing OpenRouter configuration remains available. Never put real API keys in source control. Likely credential values in Kubernetes evidence are redacted before the prompt is sent.

### InsForge dashboard

The frontend uses InsForge for password sign-in, user-owned investigation history, and live status updates. Set `NEXT_PUBLIC_INSFORGE_URL` and `NEXT_PUBLIC_INSFORGE_ANON_KEY` in `frontend/.env.local` for local development and load that file with `docker compose --env-file frontend/.env.local up --build` so Next.js receives them at image-build time. Alternatively, place those `NEXT_PUBLIC_*` values in the root `.env` and use the standard Compose command. Set the server-only `INSFORGE_URL` in `backend/.env` or the root `.env`; the backend uses it to verify the signed-in user's session before starting an investigation. The anon key is a public client setting; never use an InsForge admin API key in the frontend.

Apply the dashboard schema, realtime policies, and cluster-context history column to the linked InsForge project before using the dashboard:

```bash
npx -y @insforge/cli db migrations up --all
```

Create user accounts in the InsForge project, then sign in through the dashboard. The FastAPI investigation endpoint remains the orchestrator. It verifies the InsForge session before returning the kubeconfig context list or streaming an investigation.

The endpoint requires a valid InsForge access token:

```bash
curl -X POST http://localhost:8000/investigate \
  -H "Authorization: Bearer <InsForge access token>"
```

### Test failure scenarios

The optional fixture at `backend/tests/kubernetes/failure-scenarios.yaml` creates four intentionally unhealthy workloads in the isolated `troubleshooting-agent-test` namespace: a missing-environment CrashLoopBackOff, an invalid image tag, a bounded-memory OOMKilled pod, and a service selector mismatch. Apply it only to a disposable development cluster:

```bash
kubectl apply -f backend/tests/kubernetes/failure-scenarios.yaml
kubectl get pods,services -n troubleshooting-agent-test
```

Select that cluster in the dashboard and run an investigation. When finished, remove the test resources:

```bash
kubectl delete namespace troubleshooting-agent-test
```

The OOM test has an explicit memory limit and uses `restartPolicy: Never`; run it only in a local test cluster. Do not apply these fixtures to a production cluster.

To configure Docker Compose environment variables, copy `.env.example` to `.env` in the project root.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
# Activate the virtual environment, then:
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The backend runs at <http://localhost:8000>.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs at <http://localhost:3000>.

## Project layout

```text
backend/     FastAPI application and placeholder modules
frontend/    Next.js application
docs/        Project documentation
prompts/     Reserved for future agent prompts
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `AI_API_URL` | Full OpenAI-compatible chat-completions URL (Gemini example: `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`) |
| `AI_API_KEY` | Provider key for the configured `AI_API_URL` |
| `AI_MODEL` | Model available through the configured provider (verified Gemini example: `gemini-flash-lite-latest`) |
| `AI_TIMEOUT_SECONDS` | Provider request timeout in seconds (default: 30) |
| `OPENROUTER_API_KEY` | OpenRouter API key supplied to the backend at runtime |
| `OPENROUTER_MODEL` | OpenRouter model identifier; use a text chat model that supports JSON responses |
| `KUBECONFIG_PATH` | Optional kubeconfig path for Kubernetes investigation |
| `KUBECONFIG_HOST_PATH` | Host kubeconfig file mounted read-only into the Docker backend |
| `INSFORGE_URL` | InsForge project URL used by the backend to validate sessions |
| `NEXT_PUBLIC_API_BASE_URL` | Backend URL used by the frontend |
| `NEXT_PUBLIC_INSFORGE_URL` | InsForge project URL used by the frontend |
| `NEXT_PUBLIC_INSFORGE_ANON_KEY` | InsForge anon key used by the frontend |

