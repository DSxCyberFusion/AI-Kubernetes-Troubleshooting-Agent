CREATE TABLE public.investigations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL DEFAULT auth.uid()
    REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  namespace TEXT NOT NULL DEFAULT 'default',
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
  root_cause TEXT,
  explanation TEXT,
  fix TEXT,
  kubectl_commands TEXT[] NOT NULL DEFAULT '{}',
  confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
  error_message TEXT
);

CREATE INDEX investigations_user_created_at_idx
  ON public.investigations (user_id, created_at DESC, id DESC);

ALTER TABLE public.investigations ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA public TO authenticated;
REVOKE ALL ON TABLE public.investigations FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.investigations TO authenticated;

CREATE POLICY investigations_select_own
  ON public.investigations
  FOR SELECT
  TO authenticated
  USING (user_id = (SELECT auth.uid()));

CREATE POLICY investigations_insert_own
  ON public.investigations
  FOR INSERT
  TO authenticated
  WITH CHECK (user_id = (SELECT auth.uid()));

CREATE POLICY investigations_update_own
  ON public.investigations
  FOR UPDATE
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

INSERT INTO realtime.channels (pattern, description, enabled)
VALUES (
  'investigation:%',
  'Per-user Kubernetes investigation status updates',
  true
)
ON CONFLICT (pattern) DO UPDATE
SET description = EXCLUDED.description,
    enabled = EXCLUDED.enabled;

ALTER TABLE realtime.channels ENABLE ROW LEVEL SECURITY;

CREATE POLICY investigations_user_channel
  ON realtime.channels
  FOR SELECT
  TO authenticated
  USING (
    pattern = 'investigation:%'
    AND realtime.channel_name() = 'investigation:' || (SELECT auth.uid())::TEXT
  );

CREATE OR REPLACE FUNCTION public.publish_investigation_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  PERFORM realtime.publish(
    'investigation:' || NEW.user_id::TEXT,
    'investigation_updated',
    jsonb_build_object('id', NEW.id, 'status', NEW.status)
  );
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.publish_investigation_update() FROM PUBLIC;

CREATE TRIGGER investigations_realtime_update
  AFTER INSERT OR UPDATE OF
    status,
    namespace,
    root_cause,
    explanation,
    fix,
    kubectl_commands,
    confidence,
    error_message
  ON public.investigations
  FOR EACH ROW
  EXECUTE FUNCTION public.publish_investigation_update();