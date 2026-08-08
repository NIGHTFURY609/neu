import { Link, NavLink, Outlet, Route, Routes } from 'react-router-dom';

import { ErrorBoundary } from './components/ErrorBoundary';
import { Dashboard } from './routes/Dashboard';
import { EscalationDetail } from './routes/EscalationDetail';
import { ReviewQueue } from './routes/ReviewQueue';
import { Landing } from './routes/Landing';
import { Login } from './routes/Login';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route element={<WorkspaceLayout />}>
        <Route path="/queue" element={<ReviewQueue />} />
        <Route path="/queue/:id" element={<EscalationDetail />} />
        <Route path="/dashboard" element={<Dashboard />} />
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
        <NavLink end to="/dashboard">Workspace</NavLink>
        <NavLink to="/queue">Review queue</NavLink>
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
