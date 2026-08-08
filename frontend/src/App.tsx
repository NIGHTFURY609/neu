import { useEffect } from 'react';
import { Link, NavLink, Outlet, Route, Routes } from 'react-router-dom';

import { setAccessToken } from './auth/session';
import { supabase } from './auth/supabaseClient';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Compare } from './routes/Compare';
import { DocumentDetail } from './routes/DocumentDetail';
import { DocumentSummary } from './routes/DocumentSummary';
import { EscalationDetail } from './routes/EscalationDetail';
import { NegotiationPrep } from './routes/NegotiationPrep';
import { Portfolio } from './routes/Portfolio';
import { ReviewQueue } from './routes/ReviewQueue';
import { Search } from './routes/Search';
import { Upload } from './routes/Upload';
import { Landing } from './routes/Landing';
import { Login } from './routes/Login';

export function App() {
  // `session.ts` only ever gets a token from `Login.tsx`'s one-time snapshot at sign-in —
  // nothing kept it in sync afterwards, so a still-open tab starts sending an expired
  // token the moment it passes Supabase's token lifetime (the backend then rejects every
  // request with "Signature has expired", including a status poll, which retries forever
  // without ever getting a valid token again). supabase-js refreshes its own session
  // automatically in the background; this just relays that refreshed token into the
  // store `api/client.ts` actually reads from.
  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setAccessToken(session?.access_token ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route element={<WorkspaceLayout />}>
        <Route path="/queue" element={<ReviewQueue />} />
        <Route path="/queue/:id" element={<EscalationDetail />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/dashboard" element={<Portfolio />} />
        <Route path="/search" element={<Search />} />
        <Route path="/compare" element={<Compare />} />
        <Route path="/documents/:documentId" element={<DocumentDetail />} />
        <Route path="/documents/:documentId/summary" element={<DocumentSummary />} />
        <Route path="/documents/:documentId/negotiation" element={<NegotiationPrep />} />
      </Route>
      <Route path="*" element={<Landing />} />
    </Routes>
  );
}

function WorkspaceLayout() {
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <nav className="nav">
        <Link className="brand workspace-brand" to="/">
          CLAUSE<sup>®</sup>
          <span className="brand-sub">LEGAL INTELLIGENCE WORKSPACE</span>
        </Link>
        <NavLink to="/upload">Upload</NavLink>
        <NavLink end to="/dashboard">Workspace</NavLink>
        <NavLink to="/queue">Review queue</NavLink>
        <NavLink to="/search">Search</NavLink>
        <NavLink to="/compare">Compare</NavLink>
        <Link className="nav-exit" to="/">Exit</Link>
      </nav>

      <main id="main">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
