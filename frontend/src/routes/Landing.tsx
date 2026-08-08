import { Link } from 'react-router-dom';

import './Marketing.css';

export function Landing() {
  return (
    <main className="cla-site">
      <header className="cla-header">
        <Link className="cla-wordmark" to="/" aria-label="Clause home">
          CLAUSE<sup>®</sup>
        </Link>
        <nav aria-label="Marketing navigation">
          <a href="#platform">Platform</a>
          <a href="#workflow">Workflow</a>
          <a href="#proof">Trust</a>
        </nav>
        <div className="cla-header-actions">
          <Link className="cla-login" to="/login">Log in</Link>
          <Link className="cla-demo-button" to="/login">Enter workspace <span>+</span></Link>
        </div>
      </header>

      <section className="cla-hero" aria-labelledby="hero-title">
        <div className="cla-hero-grid" />
        <div className="cla-hero-copy">
          <p className="cla-kicker">THE LEGAL INTELLIGENCE LAYER</p>
          <h1 id="hero-title">Turn legal<br /><em>noise</em> into<br />clear moves.</h1>
          <div className="cla-hero-footer">
            <p>Clause reads every agreement, maps every obligation, and shows your team exactly what needs attention—backed by the words on the page.</p>
            <Link className="cla-round-arrow" to="/login" aria-label="Enter the Clause workspace">→</Link>
          </div>
        </div>
        <div className="cla-hero-art" aria-hidden="true">
          <div className="cla-orbit cla-orbit-one" /><div className="cla-orbit cla-orbit-two" />
          <div className="cla-contract">
            <div><span>MSA — ACME / FORGE</span><span>01 / 14</span></div>
            <i /><i /><i /><i /><i />
            <strong>Limitation of Liability <b /></strong>
            <i /><i /><i />
            <small>Source: § 12.4 <span>92% confident</span></small>
          </div>
          <div className="cla-risk-disc"><small>RISK</small><b>08</b><span>found</span></div>
          <span className="cla-tag cla-tag-top">● ANALYZING 14 CLAUSES</span>
          <span className="cla-tag cla-tag-bottom">✓ CITATION VERIFIED</span>
        </div>
      </section>

      <div className="cla-ticker" aria-label="Key principles"><div>EXPLAINABLE BY DEFAULT <b>✦</b> RISK WITH RECEIPTS <b>✦</b> ONE SOURCE OF LEGAL TRUTH <b>✦</b> EXPLAINABLE BY DEFAULT <b>✦</b> RISK WITH RECEIPTS <b>✦</b></div></div>

      <section className="cla-statement" id="platform">
        <p className="cla-kicker">01 / LESS HUNTING. MORE KNOWING.</p>
        <h2>Every legal answer<br />should come with<br /><em>a receipt.</em></h2>
        <p>AI that makes a call is useful.<br />AI that shows its work is trusted.</p>
      </section>

      <section className="cla-workspace" id="workflow">
        <div className="cla-workspace-copy"><p className="cla-kicker">02 / THE CONTROL ROOM</p><h2>Make the next<br />move obvious.</h2><p>From first upload to final approval, every finding is connected to its source, your playbook, and the people who own the next step.</p></div>
        <div className="cla-console">
          <div className="cla-console-top"><span>CLAUSE / WORKSPACE</span><b>● LIVE ANALYSIS</b><span>Q3-2026-MSA.pdf</span></div>
          <div className="cla-console-body">
            <article className="cla-document"><small>MASTER SERVICES AGREEMENT <span>PAGE 7 OF 14</span></small><h3>12. Limitation of<br />Liability</h3><p>Except for claims arising from a party’s breach of confidentiality obligations, each party’s aggregate liability shall not exceed the fees paid during the twelve months preceding the claim.</p><mark>12.4 &nbsp; Liability cap excludes confidentiality claims</mark><p>All remedies under this Agreement are cumulative and shall not preclude any other remedies available at law or equity.</p></article>
            <aside className="cla-findings"><small>CLAUSE INTELLIGENCE</small><div className="cla-score"><span>RISK SCORE</span><b>76</b><em>HIGH</em><i /></div><div className="cla-finding"><h4><i />Uncapped exposure <span>HIGH</span></h4><p>Confidentiality claims are excluded from the liability cap.</p><a href="#proof">View rationale →</a></div><div className="cla-policy"><small>POLICY / FIN-04</small><p>Flag uncapped indemnities or confidentiality liabilities.</p><b>✓ Policy matched <span>94%</span></b></div></aside>
          </div>
        </div>
      </section>

      <section className="cla-proof" id="proof">
        <p className="cla-kicker">03 / BUILT FOR THE YES</p>
        <h2>Your legal team<br />moves <em>different</em><br />with context.</h2>
        <div className="cla-benefits">
          <article><span>01</span><h3>Spot the<br />risk early.</h3><p>Surface non-standard terms, missing clauses, and policy deviations before they become a problem.</p></article>
          <article><span>02</span><h3>Prove the<br />decision.</h3><p>Every result links back to the clause, rule, and reasoning that got you there.</p></article>
          <article><span>03</span><h3>Keep the<br />whole team moving.</h3><p>Route approvals, assign actions, and give every stakeholder a shared source of truth.</p></article>
        </div>
      </section>

      <section className="cla-cta">
        <p className="cla-kicker">YOUR NEXT AGREEMENT, CLEARER</p>
        <h2>Legal intelligence<br />with <em>teeth.</em></h2>
        <p>Use the demo workspace to review real, explainable legal findings.</p>
        <Link to="/login">Enter the workspace <span>→</span></Link>
      </section>

      <footer className="cla-footer"><b>CLAUSE<sup>®</sup></b><p>Legal intelligence for the teams<br />that move business forward.</p><Link to="/login">Log in →</Link><small>© 2026 CLAUSE SYSTEMS. BUILT WITH RECEIPTS.</small></footer>
    </main>
  );
}
