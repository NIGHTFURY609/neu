import { createClient } from '@supabase/supabase-js';

/**
 * The one Supabase client, used only for `auth` (sign-in/sign-out). Nothing in this app
 * queries Supabase's PostgREST API directly — `api/client.ts` talks to our own backend,
 * which verifies the access token this client produces.
 */
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
);
