ALTER TABLE public.investigations
ADD COLUMN IF NOT EXISTS cluster_context text;