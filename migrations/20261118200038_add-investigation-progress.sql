CREATE TABLE public.investigation_progress (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_id UUID NOT NULL
    REFERENCES public.investigations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL DEFAULT auth.uid()
    REFERENCES auth.users(id) ON DELETE CASCADE,
  sequence SMALLINT NOT NULL CHECK (sequence >= 1),
  step TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'complete', 'failed')),
  message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (investigation_id, sequence)
);

CREATE INDEX investigation_progress_investigation_idx
  ON public.investigation_progress (investigation_id, sequence);

ALTER TABLE public.investigation_progress ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA public TO authenticated;
REVOKE ALL ON TABLE public.investigation_progress FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.investigation_progress TO authenticated;

CREATE POLICY investigation_progress_select_own
  ON public.investigation_progress
  FOR SELECT
  TO authenticated
  USING (
    user_id = (SELECT auth.uid())
    AND EXISTS (
      SELECT 1
      FROM public.investigations
      WHERE investigations.id = investigation_progress.investigation_id
        AND investigations.user_id = (SELECT auth.uid())
    )
  );

CREATE POLICY investigation_progress_insert_own
  ON public.investigation_progress
  FOR INSERT
  TO authenticated
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND EXISTS (
      SELECT 1
      FROM public.investigations
      WHERE investigations.id = investigation_progress.investigation_id
        AND investigations.user_id = (SELECT auth.uid())
    )
  );

CREATE POLICY investigation_progress_update_own
  ON public.investigation_progress
  FOR UPDATE
  TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));

CREATE TRIGGER investigation_progress_updated_at
  BEFORE UPDATE ON public.investigation_progress
  FOR EACH ROW
  EXECUTE FUNCTION system.update_updated_at();

CREATE OR REPLACE FUNCTION public.publish_investigation_progress()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  PERFORM realtime.publish(
    'investigation:' || NEW.user_id::TEXT,
    'investigation_progress_updated',
    jsonb_build_object(
      'investigation_id', NEW.investigation_id,
      'sequence', NEW.sequence,
      'step', NEW.step,
      'status', NEW.status
    )
  );
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.publish_investigation_progress() FROM PUBLIC;

CREATE TRIGGER investigation_progress_realtime_update
  AFTER INSERT OR UPDATE OF status, message, step
  ON public.investigation_progress
  FOR EACH ROW
  EXECUTE FUNCTION public.publish_investigation_progress();