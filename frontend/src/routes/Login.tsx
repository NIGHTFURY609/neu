import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { supabase } from '../auth/supabaseClient';
import { setAccessToken } from '../auth/session';
import './Marketing.css';

export function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const { data, error: signInError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    setSubmitting(false);
    if (signInError || !data.session) {
      setError(signInError?.message ?? 'Sign-in failed.');
      return;
    }
    setAccessToken(data.session.access_token);
    navigate('/dashboard');
  };

  return (
    <main className="cla-login-page">
      <header className="cla-login-header"><Link className="cla-wordmark" to="/">CLAUSE<sup>®</sup></Link><Link to="/" className="cla-back">← Back to site</Link></header>
      <div className="cla-login-layout">
        <section className="cla-login-copy"><p className="cla-kicker">LEGAL INTELLIGENCE, EXPLAINED</p><h1>Welcome<br />back to<br /><em>clarity.</em></h1><p>This demo workspace is connected to the review queue, risk dashboard, and explainable redlines.</p><ul><li>✓ Cited sources for every AI finding</li><li>✓ Reviewable agent decision traces</li></ul></section>
        <section className="cla-login-card" aria-labelledby="login-title">
          <header><span>CLAUSE / ACCESS PORTAL</span><b>●</b></header>
          <form onSubmit={submit}>
            <p className="cla-kicker">DEMO ACCESS</p><h2 id="login-title">Sign in.</h2>
            <label>Email address<input type="email" required autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" /></label>
            <label>Password<input type="password" required autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" /></label>
            {error && <p className="cla-login-error" role="alert">{error}</p>}
            <div><label className="cla-remember"><input type="checkbox" /> Remember me</label><button type="button">Forgot password?</button></div>
            <button className="cla-submit" type="submit" disabled={submitting}>{submitting ? 'Signing in…' : 'Enter workspace'} <span>→</span></button>
          </form>
          <footer><span>PROTECTED BY CLAUSE SECURITY</span><span>© 2026</span></footer>
        </section>
      </div>
    </main>
  );
}
