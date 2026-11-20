-- Additive support for Postal API events; existing SMTP states remain valid.
-- Existing record values, message IDs, ledger function signature and grants are preserved.
BEGIN;
ALTER TABLE outreach.messages DROP CONSTRAINT messages_state_check;
ALTER TABLE outreach.messages ADD CONSTRAINT messages_state_check CHECK (
    state = ANY (ARRAY[
      'reserved','sending','smtp_accepted','smtp_rejected',
      'outcome_unknown','suppressed','failed_before_smtp',
      'postal_api_accepted','postal_rejected','postal_outcome_unknown'
    ])
);
CREATE OR REPLACE FUNCTION public.aiaj_ledger_state(
  p_campaign text, p_email text, p_state text, p_message_id text DEFAULT NULL
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'outreach', 'pg_temp'
AS $function$
DECLARE n integer;
BEGIN
  IF p_state NOT IN (
    'sending','smtp_accepted','smtp_rejected','outcome_unknown',
    'failed_before_smtp','postal_api_accepted','postal_rejected','postal_outcome_unknown'
  ) THEN RAISE EXCEPTION 'invalid state'; END IF;
  UPDATE outreach.messages
  SET state=p_state,smtp_message_id=p_message_id,updated_at=now()
  WHERE campaign_id=p_campaign AND email=lower(trim(p_email));
  GET DIAGNOSTICS n=ROW_COUNT;
  RETURN n;
END
$function$;
COMMIT;
