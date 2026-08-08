import { NavLink, Route, Routes } from 'react-router-dom';

import { ErrorBoundary } from './components/ErrorBoundary';
import { ScaleIcon } from './components/Icons';
import { Dashboard } from './routes/Dashboard';
import { EscalationDetail } from './routes/EscalationDetail';
import { ReviewQueue } from './routes/ReviewQueue';

export function App() {
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <nav className="nav">
        <span className="brand">
          <span className="brand-mark">
            <ScaleIcon size={16} />
          </span>
          Legal Intelligence Copilot
          <span className="brand-sub">Contract risk &amp; compliance</span>
        </span>
        <NavLink to="/queue">Review queue</NavLink>
        <NavLink to="/dashboard">Compliance &amp; risk</NavLink>
      </nav>

      <main id="main">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<ReviewQueue />} />
            <Route path="/queue" element={<ReviewQueue />} />
            <Route path="/queue/:id" element={<EscalationDetail />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="*" element={<p>Not found.</p>} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
