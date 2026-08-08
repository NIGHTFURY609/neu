import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { DOC_KINDS, type DocKind } from '../api/types';
import { StageProgress } from '../components/StageProgress';
import { AlertIcon, CheckIcon, XIcon } from '../components/Icons';
import { ConfidenceBar } from '../components/Badges';
import {
  useDocumentStatus,
  useDocuments,
  useInvalidateDocumentData,
  useUploadDocument,
} from '../hooks/useDocuments';

// Hardcoded for now — the backend has no /jurisdictions route. Six strings is not worth
// an endpoint yet, but this is the place that has to change when rules gain coverage.
const JURISDICTIONS = ['US-NY', 'US-CA', 'US', 'EU', 'IN', 'GENERAL'];

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [tags, setTags] = useState('legal-team');
  const [docKind, setDocKind] = useState<DocKind>('contract');
  const [jurisdiction, setJurisdiction] = useState('US-NY');
  const [title, setTitle] = useState('');
  const [parentId, setParentId] = useState('');
  const [documentId, setDocumentId] = useState<string | null>(null);

  const upload = useUploadDocument();
  const { data: status } = useDocumentStatus(documentId);
  const invalidate = useInvalidateDocumentData();
  const { data: contracts } = useDocuments({ docKind: 'contract' });

  // A finished run makes every per-document query stale. Without this the dashboard keeps
  // showing the pre-run empty state until someone reloads.
  useEffect(() => {
    if (documentId && status?.status === 'succeeded') invalidate(documentId);
  }, [documentId, status?.status, invalidate]);

  const parent = useMemo(
    () => contracts?.find((d) => d.document_id === parentId),
    [contracts, parentId],
  );

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    const rbacTags = tags.split(',').map((t) => t.trim()).filter(Boolean);
    const result = await upload.mutateAsync({
      file,
      rbacTags,
      docKind,
      jurisdiction,
      title: title || undefined,
      parentDocumentId: parentId || undefined,
    });
    setDocumentId(result.document_id);
  };

  const uploaded = upload.data;

  return (
    <>
      <header className="page-head">
        <h1>Upload</h1>
        <p className="muted">
          A document is ingested, then the extraction, risk and redline stages run behind
          it. You can leave this page — progress is stored, not held in the browser.
        </p>
      </header>

      {documentId === null ? (
        <form className="panel resolve" onSubmit={onSubmit}>
          <label>
            <span className="field-label">File</span>
            <input
              type="file"
              accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.tiff"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
            />
          </label>
          {file ? (
            <p className="muted mono">
              {file.name} · {(file.size / 1024).toFixed(0)} KB
            </p>
          ) : null}

          <label>
            <span className="field-label">Title</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Acme MSA 2026"
            />
          </label>

          <label>
            <span className="field-label">Access tags</span>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="legal-team, finance"
              required
            />
          </label>
          <p className="muted">
            Comma separated. You can only tag an upload with tags you hold — the server
            narrows this to the intersection and refuses an empty one, because a document
            filed into a compartment you cannot read is indistinguishable from lost.
          </p>

          <div className="filters">
            <label>
              <span className="field-label">Kind</span>
              <select value={docKind} onChange={(e) => setDocKind(e.target.value as DocKind)}>
                {DOC_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {kind}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span className="field-label">Jurisdiction</span>
              <select value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
                {JURISDICTIONS.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span className="field-label">New version of</span>
              <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
                <option value="">Nothing — this is a new contract</option>
                {(contracts ?? []).map((doc) => (
                  <option key={doc.document_id} value={doc.document_id}>
                    {doc.title ?? doc.filename} (v{doc.version})
                  </option>
                ))}
              </select>
            </label>
          </div>
          {parent ? (
            <p className="muted">
              Will be recorded as version {parent.version + 1} of{' '}
              <strong>{parent.title ?? parent.filename}</strong>.
            </p>
          ) : null}

          {upload.isError ? (
            <p className="error">
              <AlertIcon size={15} /> {(upload.error as Error).message}
            </p>
          ) : null}

          <div className="actions">
            <button type="submit" disabled={!file || upload.isPending}>
              {upload.isPending ? 'Uploading…' : 'Upload and process'}
            </button>
          </div>
        </form>
      ) : (
        <>
          <section className="panel">
            <h2>Processing</h2>
            <StageProgress status={status} />
          </section>

          {uploaded ? (
            <section className="panel">
              <h2>Scan quality</h2>
              <p className="muted">
                {uploaded.pages} page(s), {uploaded.chunks} chunk(s). The worst page, not
                the average — a single bad scan caps the confidence of every redline
                written off this document.
              </p>
              <ConfidenceBar value={uploaded.min_ocr_confidence} />
              {uploaded.low_confidence_pages.length > 0 ? (
                <p className="error">
                  <AlertIcon size={15} /> Pages{' '}
                  {uploaded.low_confidence_pages.join(', ')} scanned below 0.75.
                </p>
              ) : null}
            </section>
          ) : null}

          {status?.status === 'succeeded' ? (
            <section className="panel ok">
              <h2>
                <CheckIcon /> Done
              </h2>
              <div className="actions">
                <Link to={`/documents/${documentId}`}>Open the document</Link>
                <Link to={`/documents/${documentId}/summary`}>Read the summary</Link>
                {parentId ? (
                  // The connective tissue of the whole demo: upload a revision, then see
                  // the risk delta against the version it replaces, without typing an id.
                  <Link to={`/compare?left=${parentId}&right=${documentId}`}>
                    Compare with the previous version
                  </Link>
                ) : null}
              </div>
            </section>
          ) : null}

          {status?.status === 'failed' ? (
            <section className="panel panel-error">
              <h2>
                <XIcon /> Processing stopped
              </h2>
              <p>{status.error}</p>
              <p className="muted">
                The upload itself succeeded — the file and its chunks are stored. Only the
                stages behind it stopped.
              </p>
            </section>
          ) : null}

          <div className="actions">
            <button type="button" className="secondary" onClick={() => setDocumentId(null)}>
              Upload another
            </button>
          </div>
        </>
      )}
    </>
  );
}
