import { useEffect, useState, type FormEvent } from "react";
import { gql, Q, type Alert, type Label, type QueueData } from "./api";

export function Board({ token }: { token: string }) {
  const [data, setData] = useState<QueueData | null>(null);
  const [source, setSource] = useState(""); const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState(""); const [search, setSearch] = useState("");
  const [mine, setMine] = useState(false); const [selected, setSelected] = useState<string | null>(null);
  const [assignee, setAssignee] = useState(""); const [label, setLabel] = useState<Label>("UNCERTAIN");
  const [note, setNote] = useState(""); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const me = data?.me.id;
  const filters = { source: source || null, status: status || null, severity: severity || null,
    search: search || null, assigneeId: mine ? me : null };
  async function refresh() {
    const next = await gql<QueueData>(Q.list, filters, token); setData(next);
    setSelected(current => next.alerts.some(a => a.id === current) ? current : next.alerts[0]?.id ?? null);
  }
  useEffect(() => { let active = true;
    gql<QueueData>(Q.list, { source: source || null, status: status || null, severity: severity || null, search: search || null, assigneeId: mine ? me : null }, token)
      .then(next => { if (active) { setData(next); setSelected(current => next.alerts.some(a => a.id === current) ? current : next.alerts[0]?.id ?? null); setError(""); } })
      .catch(e => { if (active) setError(e.message); }); return () => { active = false; };
  }, [token, source, status, severity, search, mine, me]);
  const alert = data?.alerts.find(a => a.id === selected);
  useEffect(() => { setAssignee(alert?.case.assigneeId ?? me ?? ""); setNote(""); }, [selected, me]);
  async function act(query: string, variables: Record<string, unknown>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { await gql(query, variables, token); await refresh(); setNotice(message); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function submitLabel(e: FormEvent) { e.preventDefault(); if (alert) void act(Q.label, { id: alert.case.id, value: label, note }, "Label saved"); }
  return <>
    <section className="metrics" aria-label="Case totals">
      {([['All cases', data?.stats.total], ['Open', data?.stats.open], ['Investigating', data?.stats.investigating], ['Resolved', data?.stats.resolved]] as const).map(([name, value]) => <div key={name}><span>{name}</span><strong>{value ?? '—'}</strong></div>)}
    </section>
    <section className="filters" aria-label="Filters">
      <input aria-label="Search alerts" placeholder="Search entity, summary, alert ID…" value={search} onChange={e => setSearch(e.target.value)} />
      <select aria-label="Source" value={source} onChange={e => setSource(e.target.value)}><option value="">All sources</option>{['transaction-stream','transaction-api','ato','text-risk'].map(s => <option key={s}>{s}</option>)}</select>
      <select aria-label="Case status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option>{['OPEN','INVESTIGATING','RESOLVED'].map(s => <option key={s}>{s}</option>)}</select>
      <select aria-label="Severity" value={severity} onChange={e => setSeverity(e.target.value)}><option value="">All severities</option>{['high','medium','low'].map(s => <option key={s}>{s}</option>)}</select>
      <label><input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} /> Assigned to me</label>
      <button onClick={() => void refresh().catch(e => setError(e.message))}>Refresh</button>
    </section>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="workspace"><section className="queue" aria-label="Alert queue"><div className="section-head"><h2>Alert queue</h2><span>{data?.alerts.length ?? 0} shown · latest 50</span></div>
      {!data && <p className="empty">Loading alerts…</p>}{data?.alerts.length === 0 && <p className="empty">No alerts match these filters.</p>}
      {data?.alerts.map(a => <button className={`alert-row ${a.id === selected ? 'selected' : ''}`} key={a.id} onClick={() => setSelected(a.id)}><span className={`severity ${a.severity}`}>{a.severity}</span><strong>{a.summary}</strong><span className="meta">{a.source} · {a.entityId}</span><span className="row-foot"><span>{a.case.status}</span><span>{new Date(a.eventTime).toLocaleString()}</span></span></button>)}
    </section><section className="detail" aria-label="Case detail">{alert ? <>
      <div className="section-head"><h2>Case #{alert.case.id}</h2><span className="pill">{alert.case.status}</span></div>
      <h3>{alert.summary}</h3><p className="meta">{alert.source} / {alert.entityType} / {alert.entityId}</p>
      <dl><dt>Model score</dt><dd>{alert.riskScore === null ? 'Unavailable — label decision only' : alert.riskScore.toFixed(6)}{scoreSource(alert)}</dd><dt>Decision threshold</dt><dd>{alert.threshold === null ? 'Label-based route' : alert.threshold.toFixed(6)}</dd><dt>Model</dt><dd>{alert.modelName} <small>{alert.modelVersion.slice(0, 16)}</small></dd><dt>Trace</dt><dd>{alert.traceId}</dd></dl>
      <div className="assignment"><label>Assignee<select aria-label="Assignee" value={assignee} onChange={e => setAssignee(e.target.value)}>{data?.analysts.map(a => <option value={a.id} key={a.id}>{a.email}</option>)}</select></label><button disabled={busy || !assignee} onClick={() => void act(Q.assign, {id: alert.case.id, analyst: assignee}, 'Case assigned')}>Assign case</button></div>
      <form onSubmit={submitLabel} className="decision"><h3>Analyst decision</h3><label>Label<select aria-label="Label" value={label} onChange={e => setLabel(e.target.value as Label)}>{['FRAUD','BENIGN','UNCERTAIN'].map(v => <option key={v}>{v}</option>)}</select></label><label>Note<textarea aria-label="Decision note" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} /></label><div className="actions"><button disabled={busy} type="submit">Save label</button><button disabled={busy || alert.case.status === 'RESOLVED'} type="button" onClick={() => void act(Q.resolve, {id: alert.case.id, note}, 'Case resolved')}>Resolve case</button></div></form>
      <h3>Labels</h3>{alert.case.labels.length ? <ul>{alert.case.labels.map(l => <li key={l.actorId}>{l.value} · analyst {l.actorId} · {l.note}</li>)}</ul> : <p className="meta">No labels yet.</p>}
      <details><summary>Signal evidence</summary><pre>{formatEvidence(alert.evidence)}</pre></details><h3>Case history</h3><ol className="timeline">{alert.case.events.map((e, i) => <li key={i}><strong>{e.action}</strong> <span>{e.note}</span><small>{new Date(e.at).toLocaleString()}{e.actorId ? ` · analyst ${e.actorId}` : ' · ingestion'}</small></li>)}</ol>
    </> : <p className="empty">Select an alert to review its case.</p>}</section></div>
  </>;
}
function formatEvidence(value: string) { try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; } }
function scoreSource(a: Alert) { try { const e = JSON.parse(a.evidence); return e.score_source === 'tfidf_lr' ? ' · TF-IDF spam probability' : ''; } catch { return ''; } }
