ALTER TABLE public.investigations
  ADD COLUMN IF NOT EXISTS affected_namespaces text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS historical_warnings jsonb NOT NULL DEFAULT '[]'::jsonb;

DROP TRIGGER IF EXISTS investigations_realtime_update
  ON public.investigations;

CREATE TRIGGER investigations_realtime_update
  AFTER INSERT OR UPDATE OF
    status,
    namespace,
    affected_namespaces,
    historical_warnings,
    root_cause,
    explanation,
    fix,
    kubectl_commands,
    confidence,
    error_message
  ON public.investigations
  FOR EACH ROW
  EXECUTE FUNCTION public.publish_investigation_update();