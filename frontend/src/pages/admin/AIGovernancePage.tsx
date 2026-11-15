import { useState } from 'react'
import { api } from '../../api/client'
import AskTutor from '../../components/AskTutor'
import { Alert, Badge, fmtDate, Loading, useLoad } from '../../components/ui'
import { useCourses } from '../staff/SourcesPage'

type Call = { id: number; at: string; user_role: string | null; purpose: string; course_id: number | null; model: string; status: string; sources: number; guard_flags: string[]; error: string | null; input_tokens: number | null; output_tokens: number | null; latency_ms: number | null }
type Full = Call & { prompt: string; response: string | null; source_chunk_ids: number[] }

export default function AIGovernancePage() {
  const st = useLoad(() => api<{ enabled: boolean; mode: string; model: string; key_configured: boolean }>('/ai/status'))
  const [status, setStatus] = useState('')
  const [purpose, setPurpose] = useState('')
  const qs = new URLSearchParams()
  if (status) qs.set('status', status)
  if (purpose) qs.set('purpose', purpose)
  const calls = useLoad(() => api<{ total: number; stats: Record<string, number>; items: Call[] }>(`/ai/calls?${qs}`), [status, purpose])
  const [open, setOpen] = useState<Full | null>(null)
  const courses = useCourses()
  const [testCourse, setTestCourse] = useState('')

  return (
    <>
      <h1>AI governance</h1>
      <p className="muted">Every AI call is traced. Answers are scoped to each course’s CLOs and PLOs (approved suggested reading is cited when it matches), off-topic questions are refused, answer keys are protected, generated material goes to instructor review, and an offline fallback keeps the app working without the model.</p>
      {st.data && (
        <Alert kind={st.data.enabled ? 'success' : 'info'}>
          Provider: Google Gemini · model <code>{st.data.model}</code> · {st.data.enabled ? 'live calls enabled' : `offline fallback (${st.data.key_configured ? 'AI_MODE=offline' : 'no API key set'})`}. Calls that fail are recorded as “fallback”.
        </Alert>
      )}
      <div className="grid">
        {Object.entries(calls.data?.stats ?? {}).map(([k, v]) => (
          <div className="card" key={k}><div className="small muted">{k}</div><div className="stat-big">{v}</div></div>
        ))}
      </div>
      <div className="row card">
        <div className="field min-w-md"><label htmlFor="fs">Outcome</label>
          <select id="fs" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option>{['ok', 'fallback', 'refused', 'blocked', 'error'].map((s) => <option key={s}>{s}</option>)}</select></div>
        <div className="field min-w-md"><label htmlFor="fp">Purpose starts with</label><input id="fp" value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="tutor. / items. / content." /></div>
      </div>
      {calls.error && <Alert onRetry={calls.reload}>{calls.error}</Alert>}
      {calls.loading ? <Loading variant="table" /> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)">
          <table>
            <thead><tr><th>When</th><th>Purpose</th><th>Outcome</th><th>Grounding</th><th>Tokens / latency</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>{calls.data?.items.map((c) => (
              <tr key={c.id}>
                <td className="small">{fmtDate(c.at)}<div className="muted">#{c.id} · {c.user_role ?? 'system'}</div></td>
                <td><code>{c.purpose}</code>{c.course_id && <div className="small muted">course #{c.course_id}</div>}</td>
                <td><Badge value={c.status} tone={c.status === 'ok' ? 'ok' : c.status === 'error' || c.status === 'blocked' ? 'bad' : 'warn'} />{c.guard_flags.map((f) => <Badge key={f} value={f} tone="bad" />)}{c.error && <div className="small muted">{c.error.slice(0, 90)}</div>}</td>
                <td>{c.sources} passages</td>
                <td className="small">{c.input_tokens ?? '–'} / {c.output_tokens ?? '–'} · {c.latency_ms ?? '–'} ms</td>
                <td><button className="secondary" onClick={async () => setOpen(await api<Full>(`/ai/calls/${c.id}`))}>Trace</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {open && (
        <div className="card mt-4">
          <div className="spread"><h2 className="m-0">Trace #{open.id}</h2><button className="secondary" onClick={() => setOpen(null)}>Close</button></div>
          <h3>Prompt (system + grounded context)</h3><pre className="plain" tabIndex={0}>{open.prompt}</pre>
          <h3>Response</h3><pre className="plain" tabIndex={0}>{open.response ?? '—'}</pre>
        </div>
      )}
      <h2>Try a grounded answer</h2>
      <div className="field">
        <label htmlFor="ai-course">Course</label>
        <select id="ai-course" className="max-w-sm" value={testCourse} onChange={(e) => setTestCourse(e.target.value)}>
          <option value="">Choose a course…</option>{courses.data?.filter((c) => c.status !== 'superseded').map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
        </select>
      </div>
      {testCourse && <AskTutor courseId={Number(testCourse)} />}
    </>
  )
}
