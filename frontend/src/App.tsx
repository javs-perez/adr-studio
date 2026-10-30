import { memo, useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSlug from 'rehype-slug';
import {
  ArrowDownToLine, ArrowLeft, ArrowUpRight, Bookmark, Check, ChevronDown,
  CircleHelp, FileText, GitPullRequest, Lightbulb, LoaderCircle, MessageSquare,
  Plus, RefreshCw, Send, Sparkles, Square, X,
} from 'lucide-react';
import { api } from './api';
import { currentSelection, locate, paintHighlights, quoteAnchor, rangeAt } from './anchors';
import type { Anchor, DocumentSummary, FeedbackItem, Job, Provocation, RankingCriterion, Review } from './types';

const isLive = (job: Job) => job.status === 'queued' || job.status === 'running';
const CRITERIA: { key: RankingCriterion; label: string }[] = [
  { key: 'decision_impact', label: 'Decision impact' },
  { key: 'evidence_quality', label: 'Evidence quality' },
  { key: 'novelty', label: 'Novelty' },
  { key: 'actionability', label: 'Actionability' },
];

const Prose = memo(function Prose({ text }: { text: string }) {
  return <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]}
    components={{
      a: ({ href, children }) => <a href={href} target={href?.startsWith('#') ? undefined : '_blank'}
        rel="noreferrer">{children}</a>,
      img: ({ alt }) => <span className="image-placeholder">[Image: {alt || 'see original document'}]</span>,
    }}>{text}</Markdown>;
});

function FeedbackEditor({ item, busy, status, onRefresh, onError, onView, onDraft }: {
  item: FeedbackItem;
  busy: boolean;
  status: ReactNode;
  onRefresh: () => Promise<void>;
  onError: (error: string) => void;
  onView: () => void;
  onDraft: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [body, setBody] = useState(item.body);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [included, setIncluded] = useState(item.included);
  useEffect(() => { setIncluded(item.included); }, [item.included]);
  useEffect(() => {
    if (!dirty) { setTitle(item.title); setBody(item.body); }
  }, [item.title, item.body, dirty]);

  async function save(values: { title?: string; body?: string; included?: boolean }) {
    setSaving(true);
    try {
      await api(`/feedback/${item.id}`, 'PATCH', values);
      await onRefresh();
      if (values.body !== undefined) setDirty(false);
    } catch (error) {
      if (values.included !== undefined) setIncluded(item.included);
      onError(error instanceof Error ? error.message : String(error));
    }
    finally { setSaving(false); }
  }
  return <section className="feedback-card" id={`feedback-${item.id}`}>
    <button className="text-button" onClick={onView}><MessageSquare size={12} /> View conversation <ArrowUpRight size={12} /></button>
    <blockquote>{item.anchor.quote}</blockquote>
    <label className="feedback-label">Heading
      <input aria-label="Feedback heading" value={title} maxLength={180}
        onChange={event => { setTitle(event.target.value); setDirty(true); }} />
    </label>
    <label className="feedback-label">Feedback
      <textarea aria-label="Feedback text" value={body} maxLength={10000} rows={5}
        placeholder="Write the feedback you want to give the ADR author."
        onChange={event => { setBody(event.target.value); setDirty(true); }} />
    </label>
    {status}
    {item.suggested_body && <details className="feedback-suggestion" open={!item.body}>
      <summary>AI suggestion · not yet approved</summary>
      <strong>{item.suggested_title}</strong>
      <div className="message-body"><Prose text={item.suggested_body} /></div>
      <button className="text-button" onClick={() => {
        setTitle(item.suggested_title); setBody(item.suggested_body); setDirty(true);
      }}>Use suggestion in editor</button>
    </details>}
    <div className="feedback-actions">
      <button className="text-button" disabled={busy} onClick={onDraft}><Sparkles size={12} /> Draft with AI</button>
      <button className="button primary" disabled={saving || !title.trim() || !body.trim() || !dirty}
        onClick={() => void save({ title: title.trim(), body: body.trim() })}>
        {saving ? 'Saving…' : 'Save feedback'}</button>
    </div>
    <label className="feedback-include"><input type="checkbox" checked={included} disabled={saving}
      onChange={event => {
        setIncluded(event.target.checked); void save({ included: event.target.checked });
      }} /> Include saved feedback in review</label>
    <small>{dirty ? 'Unsaved edits — save before switching tabs or combining.' :
      item.body ? 'Saved locally. Only saved text is combined.' : 'Draft only. Save your feedback to include it.'}</small>
  </section>;
}

function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [documentId, setDocumentId] = useState(localStorage.getItem('adr-document') ?? '');
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [showImport, setShowImport] = useState(false);
  const [url, setUrl] = useState('');
  const [candidates, setCandidates] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [selection, setSelection] = useState<Anchor | null>(null);
  const [draftAnchor, setDraftAnchor] = useState<Anchor | null>(null);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [askAi, setAskAi] = useState(true);
  const [sending, setSending] = useState(false);
  const [conversationTab, setConversationTab] = useState<'open' | 'resolved' | 'feedback'>('open');
  const [showCombined, setShowCombined] = useState(false);
  const [filter, setFilter] = useState('open');
  const [showOutline, setShowOutline] = useState(false);
  const [focus, setFocus] = useState<Anchor | undefined>();
  const articleRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const reviewIdRef = useRef(documentId);
  reviewIdRef.current = documentId;

  const refresh = useCallback(async () => {
    if (!documentId) return;
    const value = await api<Review>(`/documents/${documentId}`);
    if (reviewIdRef.current === documentId) setReview(value);
  }, [documentId]);

  useEffect(() => {
    api<DocumentSummary[]>('/documents').then(items => {
      setDocuments(items);
      setDocumentId(id => items.some(item => item.id === id) ? id : items[0]?.id ?? '');
      if (!items.length) setShowImport(true);
      setLoading(false);
    }).catch(e => { setError(String(e.message)); setLoading(false); });
  }, []);

  useEffect(() => {
    if (!documentId) return;
    localStorage.setItem('adr-document', documentId);
    setReview(null);
    setActiveThread(null);
    setDraftAnchor(null);
    setSelection(null);
    setDraft('');
    setFocus(undefined);
    setConversationTab('open');
    setShowCombined(false);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await refresh();
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : String(e));
      }
      if (!disposed) timer = setTimeout(poll, 1200);
    }
    void poll();
    api(`/documents/${documentId}/research`, 'POST', { initial: true })
      .catch(e => { if (!disposed) setError(e.message); });
    return () => { disposed = true; clearTimeout(timer); };
  }, [documentId, refresh]);

  const thread = review?.threads.find(t => t.id === activeThread);
  const researchJob = review?.jobs.find(j => j.kind === 'research');
  const chatJob = activeThread
    ? review?.jobs.find(j => j.kind === 'chat' && j.thread_id === activeThread)
    : undefined;
  const anchor = draftAnchor ?? thread?.anchor;

  useEffect(() => {
    if (articleRef.current && review) {
      paintHighlights(articleRef.current, review.threads.filter(t => !t.resolved).map(t => t.anchor),
        focus ?? anchor);
    }
  }, [review, anchor, focus]);

  async function act(action: () => Promise<unknown>) {
    setError('');
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  async function importDocument(path?: string) {
    setImporting(true);
    setError('');
    try {
      const result = await api<{ id?: string; candidates?: string[] }>('/import', 'POST', { url, path });
      if (result.candidates) {
        setCandidates(result.candidates);
        if (!result.candidates.length) setError('No Markdown documents were found in this PR.');
      } else if (result.id) {
        setDocuments(await api<DocumentSummary[]>('/documents'));
        setDocumentId(result.id);
        setShowImport(false);
        setCandidates([]);
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setImporting(false); }
  }

  function jump(target: Anchor) {
    const root = articleRef.current;
    if (!root) return;
    const position = locate(root.textContent ?? '', target);
    const range = position && rangeAt(root, ...position);
    if (!range) {
      setError('This saved passage could not be located in the rendered document. Its quoted text is preserved.');
      return;
    }
    setFocus(target);
    range.startContainer.parentElement?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function startComment(target: Anchor, initial = '') {
    setConversationTab('open');
    setDraftAnchor(target);
    setActiveThread(null);
    setDraft(initial);
    setSelection(null);
    setFocus(target);
    window.getSelection()?.removeAllRanges();
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  async function promoteFeedback(threadId: string) {
    await act(async () => {
      await api(`/threads/${threadId}/feedback`, 'POST');
      setConversationTab('feedback');
      setShowCombined(false);
    });
  }

  function discussProvocation(p: Provocation) {
    const target = articleRef.current && review &&
      quoteAnchor(articleRef.current, p.quote, review.document.sha);
    if (!target) {
      setError('This provocation quotes Markdown or repeated text. Select its passage in the ADR to start a linked conversation.');
      return;
    }
    jump(target);
    startComment(target, `${p.question}\n\nResearch context: ${p.evidence}\n\nSources:\n${p.sources.map(s => s.url).join('\n')}`);
  }

  async function send() {
    if (!draft.trim() || sending || !anchor) return;
    setSending(true);
    setError('');
    try {
      if (draftAnchor) {
        const result = await api<{ thread_id: string }>(`/documents/${documentId}/threads`, 'POST',
          { anchor: draftAnchor, message: draft, ask_ai: askAi });
        setActiveThread(result.thread_id);
        setDraftAnchor(null);
      } else {
        await api(`/threads/${activeThread}/messages`, 'POST', { message: draft, ask_ai: askAi });
      }
      setDraft('');
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSending(false); }
  }

  function JobStatus({ job }: { job?: Job }) {
    if (!job) return null;
    if (isLive(job)) return <div className="job-status" role="status">
      <LoaderCircle size={14} className="spin" />
      <span>{job.status === 'queued' ? 'Queued for Copilot…' : job.activity}</span>
      <button className="icon-button" title="Stop job" onClick={() => void act(() =>
        api(`/jobs/${job.id}/cancel`, 'POST'))}><Square size={12} /></button>
    </div>;
    if (job.status === 'failed' || job.status === 'cancelled') return <div className="job-error" role="alert">
      <p>{job.error}</p><button className="text-button" onClick={() => void act(() =>
        api(`/jobs/${job.id}/retry`, 'POST'))}><RefreshCw size={12} /> Retry</button>
    </div>;
    return null;
  }

  const visibleThreads = review?.threads.filter(t => t.resolved === (conversationTab === 'resolved')) ?? [];
  const visibleProvocations = review?.provocations.filter(p => filter === 'all' || p.status === filter) ?? [];
  const conversationCounts = {
    open: review?.threads.filter(t => !t.resolved).length ?? 0,
    resolved: review?.threads.filter(t => t.resolved).length ?? 0,
    feedback: review?.feedback_items.length ?? 0,
  };

  return <div className="app-shell">
    <header className="app-header">
      <div className="brand"><span className="brand-symbol"><FileText size={20} /></span>
        <strong>ADR<span>Studio</span></strong><span className="brand-divider" />
        <span className="header-subtitle">A space to think decisions through</span>
      </div>
      <div className="header-actions">
        <span className="local-badge"><span /> Local workspace</span>
        <button className="button quiet" onClick={() => setShowImport(true)}><Plus size={15} /> Open ADR</button>
        {review && <a className="button export" href={`/api/documents/${documentId}/export`} download>
          <ArrowDownToLine size={15} /> Export review</a>}
      </div>
    </header>
    {error && <div className="error-banner" role="alert"><span>{error}</span>
      <button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button>
    </div>}
    {!review ? <main className="welcome">
      <div className="welcome-icon"><FileText size={34} /></div>
      <p className="eyebrow">READ · QUESTION · CONNECT</p>
      <h1>Good decisions deserve<br />a little conversation.</h1>
      <p>Read an ADR alongside company context.<br />Highlight a word, question a tradeoff, follow a better precedent.</p>
      {loading || documentId ? <LoaderCircle className="spin" /> :
        <button className="button primary" onClick={() => setShowImport(true)}>Open your first ADR <ArrowUpRight size={16} /></button>}
    </main> : <>
      <div className="document-bar">
        <div className="document-identity"><GitPullRequest size={16} />
          <a href={review.document.pr_url} target="_blank" rel="noreferrer">
            {review.document.repo}#{review.document.pr_url.split('/').pop()}</a>
          <span className="slash">/</span><span>{review.document.path}</span>
        </div>
        <div className="revision"><span className="pinned-dot" />
          <a href={review.document.url} target="_blank" rel="noreferrer" title="Immutable source revision">
            Pinned to {review.document.sha.slice(0, 7)}</a>
          {documents.length > 1 && <select aria-label="Saved review" value={documentId}
            onChange={event => setDocumentId(event.target.value)}>
            {documents.map(doc => <option key={doc.id} value={doc.id}>{doc.title} · {doc.sha.slice(0, 7)}</option>)}
          </select>}
        </div>
      </div>
      <main className="workspace">
        <aside className="pane provocations-pane">
          <div className="pane-header">
            <div><div className="pane-title"><Lightbulb size={17} /><h2>Provocations</h2>
              <span className="count">{review.provocations.length}</span></div>
              <p>Other ideas worth bringing into the room.</p></div>
            <button className="icon-button" title="Research the whole ADR again" disabled={researchJob && isLive(researchJob)}
              onClick={() => void act(() => api(`/documents/${documentId}/research`, 'POST', {}))}>
              <RefreshCw size={15} /></button>
          </div>
          <div className="pane-scroll">
            <div className="research-intro"><Sparkles size={16} /><p>Not a verdict. Researched questions,
              connected to your company’s documentation.</p></div>
            <JobStatus job={researchJob} />
            {researchJob?.status === 'complete' && <details className="research-summary">
              <summary>Research notes <ChevronDown size={12} /></summary>
              <p>{researchJob.answer}</p>
              <small>{new Date(researchJob.created_at).toLocaleString()}</small>
            </details>}
            <details className="ranking-policy">
              <summary>Highest-ranked first <ChevronDown size={12} /></summary>
              <p>AI assessments, not probabilities. Each criterion needs at least {review.ranking_policy.minimum}/{review.ranking_policy.scale_max} to publish.</p>
              <p>{CRITERIA.map(c => `${c.label} ${review.ranking_policy.weights[c.key]}%`).join(' · ')}.</p>
              <p>Ties favor decision impact, then evidence quality.</p>
            </details>
            {review.archived_research_count > 0 && <p className="archive-note">
              {review.archived_research_count} vault-exposed research run(s) archived. Conversations preserved.
            </p>}
            <div className="filter-row">
              {['open', 'saved', 'all'].map(item => <button key={item}
                className={filter === item ? 'filter active' : 'filter'}
                onClick={() => setFilter(item)}>{item === 'open' ? 'To explore' : item === 'saved' ? 'Saved' : 'All'}</button>)}
            </div>
            {!visibleProvocations.length && <div className="empty-state compact">
              <CircleHelp size={25} /><h3>{researchJob && isLive(researchJob) ? 'Following the evidence…' : 'Room for another perspective'}</h3>
              <p>{researchJob && isLive(researchJob) ?
                'Copilot is reading internal sources. Supported provocations will appear here when the run finishes.' :
                filter === 'saved' ? 'Bookmark a provocation to keep it in view.' :
                'Refresh research to look for precedents, documented standards, and useful tradeoffs.'}</p>
            </div>}
            {visibleProvocations.map(p => <section key={p.id} className={`provocation-card ${p.status === 'dismissed' ? 'dismissed' : ''}`}>
              <div className="card-meta"><span className={`category ${p.category}`}>{p.category}</span>
                <span className="rank-badge">#{p.rank} · {p.score !== null ? `${p.score}/100` : 'Unranked'}</span></div>
              <h3>{p.title}</h3>
              {p.ranking && <details className="ranking-details">
                <summary>Why this rank? <ChevronDown size={12} /></summary>
                {CRITERIA.map(({ key, label }) => {
                  const assessment = p.ranking![key];
                  return <div key={key}><strong>{label} <span>{assessment.score}/{review.ranking_policy.scale_max}</span></strong>
                    <p>{assessment.reason}</p></div>;
                })}
              </details>}
              <button className="quoted-passage" title="Find this passage in the ADR" onClick={() => {
                const target = articleRef.current && quoteAnchor(articleRef.current, p.quote, review.document.sha);
                if (target) jump(target);
                else setError('Could not uniquely locate this Markdown quote. Use the ADR’s text to find the passage.');
              }}>{p.quote}</button>
              <div className="card-evidence"><Prose text={p.evidence} /></div>
              <p className="provocation-question">{p.question}</p>
              <div className="source-list">{p.sources.map((source, i) => <details key={source.url}>
                <summary><span className="source-number">{i + 1}</span><span>{source.title}</span></summary>
                <a href={source.url} target="_blank" rel="noreferrer">Open source <ArrowUpRight size={12} /></a>
                <small>Retrieved {new Date(source.retrieved_at).toLocaleDateString()}</small>
                <pre>{source.excerpt}</pre>
              </details>)}</div>
              <div className="card-actions">
                <button className="text-button" onClick={() => discussProvocation(p)}><MessageSquare size={13} /> Discuss</button>
                <div><button className={`icon-button ${p.status === 'saved' ? 'bookmarked' : ''}`}
                  title={p.status === 'saved' ? 'Unsave' : 'Save provocation'} onClick={() => void act(() =>
                    api(`/provocations/${p.id}`, 'PATCH', { status: p.status === 'saved' ? 'open' : 'saved' }))}><Bookmark size={14} /></button>
                  <button className="icon-button" title={p.status === 'dismissed' ? 'Restore' : 'Dismiss provocation'} onClick={() => void act(() =>
                    api(`/provocations/${p.id}`, 'PATCH', { status: p.status === 'dismissed' ? 'open' : 'dismissed' }))}>
                    {p.status === 'dismissed' ? <RefreshCw size={14} /> : <X size={14} />}</button></div>
              </div>
            </section>)}
            <div className="sources-footer"><span className="pinned-dot" /> GitHub{review.slack_enabled ? ' · Public technical Slack' : ' · Slack disabled'}
              <p>Read-only research. No vault access or Microsoft 365.</p></div>
          </div>
        </aside>

        <section className="document-pane pane">
          <div className="reading-toolbar">
            <span><FileText size={16} /><span className="file-name" title={review.document.path}>{review.document.path.split('/').pop()}</span></span>
            <button className="text-button" onClick={() => setShowOutline(!showOutline)}>Contents <ChevronDown size={13} /></button>
          </div>
          {showOutline && <nav className="outline" aria-label="Document contents">
            {Array.from(articleRef.current?.querySelectorAll('h2, h3') ?? []).map(heading =>
              <a href={`#${heading.id}`} key={heading.id} onClick={() => setShowOutline(false)}>{heading.textContent}</a>)}
          </nav>}
          <div className="document-scroll" onMouseUp={() => {
            if (articleRef.current) setSelection(currentSelection(articleRef.current, review.document.sha));
          }} onKeyUp={() => {
            if (articleRef.current) setSelection(currentSelection(articleRef.current, review.document.sha));
          }}>
            <div className="reading-note"><span /> Select any passage to start a conversation.</div>
            <article ref={articleRef} className="adr-prose"><Prose text={review.document.body} /></article>
            <div className="document-end">END OF DECISION RECORD <span /> Keep the questions going.</div>
          </div>
          {selection && <div className="selection-toolbar" role="toolbar" aria-label="Selected text actions"
            onMouseDown={event => event.preventDefault()}>
            <span>{selection.quote.length > 36 ? `${selection.quote.slice(0, 36)}…` : selection.quote}</span>
            <button onClick={() => startComment(selection)}><MessageSquare size={14} /> Comment</button>
            <button disabled={researchJob && isLive(researchJob)} onClick={() => {
              void act(() => api(`/documents/${documentId}/research`, 'POST', { selection: selection.quote }));
              setSelection(null);
            }}><Sparkles size={14} /> Provoke</button>
            <button className="icon-button" aria-label="Clear selection" onClick={() => setSelection(null)}><X size={14} /></button>
          </div>}
        </section>

        <aside className="pane conversations-pane">
          <div className="pane-header">
            <div><div className="pane-title"><MessageSquare size={17} /><h2>Conversations</h2>
              <span className="count">{review.threads.filter(t => !t.resolved).length}</span></div>
              <p>A closer look, one passage at a time.</p></div>
          </div>
          <div className="filter-row thread-filters" role="tablist" aria-label="Conversation sections">
            {(['open', 'resolved', 'feedback'] as const).map(tab =>
              <button key={tab} role="tab" aria-selected={conversationTab === tab}
                className={`filter ${conversationTab === tab ? 'active' : ''}`}
                onClick={() => {
                  setConversationTab(tab); setActiveThread(null); setDraftAnchor(null);
                  setDraft(''); setFocus(undefined); setShowCombined(false);
                }}>
                {tab === 'open' ? <MessageSquare size={14} /> : tab === 'resolved' ? <Check size={14} /> : <FileText size={14} />}
                {tab === 'open' ? 'Open' : tab === 'resolved' ? 'Resolved' : 'Feedback'}
                {conversationCounts[tab] > 0 && <span className="count">{conversationCounts[tab]}</span>}
              </button>)}
          </div>
          {conversationTab === 'feedback' ? <>
            <div className="feedback-toolbar">
              <button className="text-button" disabled={!review.feedback_review}
                onClick={() => setShowCombined(!showCombined)}>
                <FileText size={13} /> {showCombined ? 'Edit feedback items' : 'Combine review'}
              </button>
              {review.feedback_review && <a className="text-button"
                href={`/api/documents/${documentId}/feedback/export`} download>
                <ArrowDownToLine size={13} /> Export feedback</a>}
            </div>
            <div className="pane-scroll feedback-scroll">
              {showCombined ? <div className="combined-review">
                <p className="feedback-note">Only saved, included items. Nothing is posted to GitHub.</p>
                <div className="message-body"><Prose text={review.feedback_review} /></div>
              </div> : <>
                <p className="feedback-note">Promote a conversation, refine the feedback, then combine the items you want to share.</p>
                {!review.feedback_items.length && <div className="empty-state compact">
                  <MessageSquare size={24} /><h3>Turn discussion into feedback.</h3>
                  <p>Open a conversation and choose “Add feedback”. You can edit the draft before including it in a review.</p>
                </div>}
                {review.feedback_items.map(item => {
                  const job = review.jobs.find(j => j.kind === 'feedback' && j.thread_id === item.thread_id);
                  const busy = review.jobs.some(j => j.thread_id === item.thread_id && isLive(j));
                  return <FeedbackEditor key={item.id} item={item} busy={busy} status={<JobStatus job={job} />}
                    onRefresh={refresh} onError={setError}
                    onDraft={() => void act(() => api(`/feedback/${item.id}/draft`, 'POST'))}
                    onView={() => {
                      const origin = review.threads.find(t => t.id === item.thread_id);
                      setActiveThread(item.thread_id); setDraftAnchor(null); setDraft('');
                      setConversationTab(origin?.resolved ? 'resolved' : 'open'); jump(item.anchor);
                    }} />;
                })}
              </>}
            </div>
          </> : anchor ? <>
            <div className="thread-toolbar">
              <button className="text-button" onClick={() => {
                setActiveThread(null); setDraftAnchor(null); setDraft(''); setFocus(undefined);
              }}><ArrowLeft size={14} /> All threads</button>
              {thread && <button className="text-button" onClick={() => void act(() =>
                api(`/threads/${thread.id}`, 'PATCH', { resolved: !thread.resolved }))}>
                <Check size={14} /> {thread.resolved ? 'Reopen' : 'Resolve'}</button>}
              {thread && <button className="text-button" disabled={review.jobs.some(j => j.thread_id === thread.id && isLive(j))}
                onClick={() => void promoteFeedback(thread.id)}><Plus size={13} /> Add feedback</button>}
            </div>
            <button className="thread-anchor" onClick={() => jump(anchor)}>
              <span>SELECTED PASSAGE <ArrowUpRight size={12} /></span><blockquote>{anchor.quote}</blockquote>
            </button>
            <div className="messages pane-scroll" aria-live="polite">
              {draftAnchor && <div className="empty-state compact"><Sparkles size={23} /><h3>What caught your attention?</h3>
                <p>Challenge a decision, unpack a word, or ask how this compares with another approach.</p></div>}
              {thread?.messages.map(message => <div className={`message ${message.role}`} key={message.id}>
                <div className="message-author"><span className={`avatar ${message.role}`}>{message.role === 'user' ? 'You' : <Sparkles size={12} />}</span>
                  <strong>{message.role === 'user' ? 'You' : 'Copilot'}</strong>
                  <small>{message.status === 'complete' ? new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : message.status}</small></div>
                <div className="message-body">{message.content ? <Prose text={message.content} /> :
                  message.status === 'streaming' ? <span className="thinking">Thinking with the document…</span> :
                    <span className="thinking">No completed reply.</span>}</div>
              </div>)}
              <JobStatus job={chatJob} />
            </div>
            <form className="composer" onSubmit={event => { event.preventDefault(); void send(); }}>
              <span className="composer-label">{draftAnchor ? 'Add a comment' : 'Reply to this conversation'}</span>
              <textarea ref={textareaRef} aria-label="Your comment" value={draft} maxLength={20000}
                onChange={event => setDraft(event.target.value)} placeholder="What would you like to explore?"
                onKeyDown={event => {
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); }
                }} />
              <div className="composer-footer"><label><input type="checkbox" checked={askAi}
                onChange={event => setAskAi(event.target.checked)} /><Sparkles size={12} /> Ask Copilot</label>
                <button className="send-button" type="submit" disabled={!draft.trim() || sending || !!(chatJob && isLive(chatJob))}
                  aria-label={askAi ? 'Send to Copilot' : 'Save comment'}>{sending ? <LoaderCircle size={15} className="spin" /> : <Send size={15} />}</button></div>
              <small>{askAi ? 'Uses your Copilot account · ⌘/Ctrl + Enter to send' : 'Saved as a note, without an AI response'}</small>
            </form>
          </> : <>
            <div className="pane-scroll">
              {!visibleThreads.length && <div className="empty-state conversation-empty">
                <div className="conversation-illustration"><MessageSquare size={32} /></div>
                <h3>{conversationTab === 'resolved' ? 'Nothing resolved yet' : 'The conversation starts\nwith a highlight.'}</h3>
                <p>Select a decision, a sentence,<br />or even a single word in the ADR.</p>
                <div className="example-question">“Have we solved this elsewhere?”</div>
                <div className="example-question">“What are we trading away here?”</div>
                <small>Your notes and conversations stay saved locally.</small>
              </div>}
              {visibleThreads.map(t => <button key={t.id} className="thread-preview" onClick={() => {
                setActiveThread(t.id); setDraftAnchor(null); setDraft(''); jump(t.anchor);
              }}>
                <blockquote>{t.anchor.quote}</blockquote>
                <p>{t.messages.find(m => m.role === 'user')?.content}</p>
                <span><MessageSquare size={12} /> {t.messages.length} messages <ArrowUpRight size={12} /></span>
              </button>)}
            </div>
          </>}
        </aside>
      </main>
      <footer className="statusbar"><span><span className="pinned-dot" /> Saved locally · SQLite</span>
        <span>Provocations are questions, not conclusions.</span><span>Live AI via your Copilot CLI</span></footer>
    </>}
    {showImport && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="import-title">
      <button className="icon-button modal-close" aria-label="Close import" onClick={() => setShowImport(false)}><X size={18} /></button>
      <div className="welcome-icon"><GitPullRequest size={24} /></div><h2 id="import-title">Bring a decision into focus.</h2>
      <p>Open a Markdown ADR from a GitHub pull request. We’ll save an immutable snapshot and begin researching company context.</p>
      <form onSubmit={event => { event.preventDefault(); void importDocument(); }}>
        <label htmlFor="pr-url">Pull request URL</label>
        <input id="pr-url" type="url" required value={url} placeholder="https://github.com/owner/repository/pull/123"
          onChange={event => { setUrl(event.target.value); setCandidates([]); }} />
        <button className="button primary" disabled={importing}>{importing ? <LoaderCircle size={16} className="spin" /> : <ArrowUpRight size={16} />}
          {importing ? 'Reading GitHub…' : 'Open & research ADR'}</button>
      </form>
      {candidates.length > 0 && <div className="candidate-list"><p>Choose the document to review:</p>
        {candidates.map(path => <button key={path} disabled={importing} onClick={() => void importDocument(path)}>{path}</button>)}
      </div>}
      <small>GitHub and Copilot use your existing local logins.<br />Opening a new revision creates a separate review, preserving earlier annotations.</small>
    </section></div>}
  </div>;
}

export default App;
