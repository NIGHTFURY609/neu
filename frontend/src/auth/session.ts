/**
 * The access token, and nothing else.
 *
 * A plain module, not React context, on purpose. `client.ts` is a plain module too, and
 * keeping the token reachable from it without a hook is what lets the existing tests
 * render components bare — no provider wrapper, no auth mocking. Putting the token only
 * in context would mean every test that renders a component touching the API needs a
 * provider, which is a lot of churn to express something no test cares about.
 *
 * This is the seam the login UI plugs into: call `setAccessToken` when a session starts
 * or refreshes, and `setAccessToken(null)` on sign-out. Nothing here knows or cares where
 * the token came from.
 */

const STORAGE_KEY = 'lic.token';

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private browsing, or a test environment without storage. Not fatal — the token
    // simply does not survive a reload.
    return null;
  }
}

let token: string | null = readStored();
const listeners = new Set<() => void>();

export function getAccessToken(): string | null {
  return token;
}

export function setAccessToken(next: string | null): void {
  token = next;
  try {
    if (next === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Ignore: an unwritable store means no persistence, not a broken session.
  }
  for (const listener of listeners) listener();
}

export function onTokenChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Called by the API client on any 401. Clearing here rather than at the call site means
 * an expired token is dropped once, wherever it is noticed, instead of every subsequent
 * request retrying with a credential already known to be dead.
 */
export function onUnauthorized(): void {
  if (token !== null) setAccessToken(null);
}
