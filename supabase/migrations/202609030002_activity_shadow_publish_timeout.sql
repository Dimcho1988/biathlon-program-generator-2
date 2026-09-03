-- Large activity shadow payloads can exceed the inherited 8 second
-- service-role statement timeout while PostgreSQL parses and TOAST-compresses
-- the immutable input and derived JSONB documents. Keep the exemption scoped
-- to this idempotent publishing function rather than widening the role timeout.
alter function public.publish_onflows_activity_shadow(
  text, text, text, text, text, jsonb, text, text, text,
  text, text, text, text, text, jsonb
) set statement_timeout to '45s';
