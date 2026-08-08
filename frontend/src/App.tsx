import { NavLink, Route, Routes } from 'react-router-dom';

import { ErrorBoundary } from './components/ErrorBoundary';
import { ScaleIcon } from './components/Icons';
import { Compare } from './routes/Compare';
import { DocumentDetail } from './routes/DocumentDetail';
import { DocumentSummary } from './routes/DocumentSummary';
import { EscalationDetail } from './routes/EscalationDetail';
import { NegotiationPrep } from './routes/NegotiationPrep';
import { Portfolio } from './routes/Portfolio';
import { ReviewQueue } from './routes/ReviewQueue';
import { Search } from './routes/Search';
import { Upload } from './routes/Upload';

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
        {/* Upload first: it is the front door, and until now it had no door at all. */}
        <NavLink to="/upload">Upload</NavLink>
        <NavLink to="/dashboard">Compliance &amp; risk</NavLink>
        <NavLink to="/queue">Review queue</NavLink>
        <NavLink to="/search">Search</NavLink>
        <NavLink to="/compare">Compare</NavLink>
      </nav>

      <main id="main">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<ReviewQueue />} />
            <Route path="/queue" element={<ReviewQueue />} />
            <Route path="/queue/:id" element={<EscalationDetail />} />
            <Route path="/upload" element={<Upload />} />
            <Route path="/dashboard" element={<Portfolio />} />
            <Route path="/search" element={<Search />} />
            <Route path="/compare" element={<Compare />} />
            <Route path="/documents/:documentId" element={<DocumentDetail />} />
            <Route path="/documents/:documentId/summary" element={<DocumentSummary />} />
            <Route path="/documents/:documentId/negotiation" element={<NegotiationPrep />} />
            <Route path="*" element={<p>Not found.</p>} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}
