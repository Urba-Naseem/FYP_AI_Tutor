import { Check, CheckCircle2, ListChecks, Pencil, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { askConfirm } from '../../components/dialogApi'
import Markdown from '../../components/Markdown'
import AutomationNote from '../../components/AutomationNote'
import ScopeHint, { AssignHint } from '../../components/ScopeHint'
import { toast } from '../../components/toast'
import { Alert, Badge, EmptyState, Loading, useAction, useLoad } from '../../components/ui'

type Item = { id: number; clo_id: number; clo_code: string; purpose: string; stem: string; options: string[]; answer_index: number; explanation: string; hints: string[]; difficulty: number; status: string; origin: string; version_no: number; review_note: string | null }
type Cov = { min_items_per_clo: number; items_per_clo: number; published: boolean; complete: boolean; short: string[]; clos: { clo_id: number; clo_code: string; description: string; approved: number; pending: number; difficulties: number[]; meets_minimum: boolean }[] }

const TYPE_LABEL: Record<string, string> = { diagnostic: 'Diagnostic', formative: 'Formative', reteach: 'Reteach' }

/** ONE approval for the whole questionnaire (all types): everything still in the drafts is approved together. */
function ApproveQuestionnaire({ courseId, drafts, onDone }: { courseId: string; drafts: Item[]; onDone: () => void }) {
  const act = useAction()
  const counts = Object.entries(drafts.reduce<Record<string, number>>((t, it) => ({ ...t, [it.purpose]: (t[it.purpose] ?? 0) + 1 }), {}))
  const summary = counts.map(([p, n]) => `${n} ${(TYPE_LABEL[p] ?? p).toLowerCase()}`).join(', ')
  return (
    <section className="card approve-bar" aria-labelledby="approve-title">
      <h2 id="approve-title" className="mt-0">Approve the questionnaire</h2>
      <p className="card-desc">{drafts.length} draft question{drafts.length === 1 ? '' : 's'} ({summary}). Delete any you do not want above, then approve them all at once. The diagnostic goes live when every CLO has enough approved questions.</p>
      <Alert>{act.error}</Alert>
      <div className="card-actions">
        <button disabled={act.busy} aria-busy={act.busy || undefined} onClick={async () => {
          if (!(await askConfirm({ title: 'Approve the whole questionnaire?', message: `All ${drafts.length} draft questions (${summary}) will be approved together and students can be tested on them.`, confirmLabel: 'Approve questionnaire' }))) return
          const r = await act.run(() => api<{ approved: number; diagnostic_published: boolean }>(`/items/approve-all/${courseId}`, { body: {} }))
          if (r) { toast(`${r.approved} questions approved${r.diagnostic_published ? ' — the diagnostic is live' : ''}`); onDone() }
        }}><CheckCircle2 aria-hidden="true" />Approve questionnaire ({drafts.length})</button>
      </div>
    </section>
  )
}

function ItemCard({ it, onDone }: { it: Item; onDone: () => void }) {
  const act = useAction()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ stem: it.stem, options: it.options.join('\n'), answer_index: it.answer_index, explanation: it.explanation, hints: it.hints.join('\n') })
  async function save() {
    const body = { stem: draft.stem, options: draft.options.split('\n').map((s) => s.trim()).filter(Boolean), answer_index: Number(draft.answer_index), explanation: draft.explanation, hints: draft.hints.split('\n').filter(Boolean) }
    if (await act.run(() => api(`/items/${it.id}`, { method: 'PATCH', body }))) { setEditing(false); onDone() }
  }
  return (
    <article className="card">
      <div className="spread">
        <div className="row"><strong>{it.clo_code}</strong><Badge value={TYPE_LABEL[it.purpose] ?? it.purpose} tone="info" /><Badge value={`v${it.version_no}`} /><Badge value={it.status} /><Badge value={it.origin} /><span className="small muted">Difficulty {it.difficulty}</span></div>
        {it.status !== 'rejected' && it.status !== 'retired' && <button className="secondary small" onClick={() => setEditing(!editing)}>{editing ? 'Cancel editing' : <><Pencil aria-hidden="true" />Edit item</>}</button>}
      </div>
      {editing ? (
        <div className="stack">
          <div><label htmlFor={`ib-stem-${it.id}`}>Question</label><textarea id={`ib-stem-${it.id}`} value={draft.stem} onChange={(e) => setDraft({ ...draft, stem: e.target.value })} /></div>
          <div><label htmlFor={`ib-opts-${it.id}`}>Options (one per line)</label><textarea id={`ib-opts-${it.id}`} value={draft.options} onChange={(e) => setDraft({ ...draft, options: e.target.value })} /></div>
          <div><label htmlFor={`ib-ans-${it.id}`}>Correct option number (0–3)</label><input id={`ib-ans-${it.id}`} type="number" min={0} max={3} value={draft.answer_index} onChange={(e) => setDraft({ ...draft, answer_index: Number(e.target.value) })} /></div>
          <div><label htmlFor={`ib-exp-${it.id}`}>Explanation</label><textarea id={`ib-exp-${it.id}`} value={draft.explanation} onChange={(e) => setDraft({ ...draft, explanation: e.target.value })} /></div>
          <div><label htmlFor={`ib-hints-${it.id}`}>Hints (one per line, must not reveal the answer)</label><textarea id={`ib-hints-${it.id}`} value={draft.hints} onChange={(e) => setDraft({ ...draft, hints: e.target.value })} /></div>
          <div className="card-actions"><button onClick={save} disabled={act.busy} aria-busy={act.busy || undefined}>{it.status === 'approved' ? 'Save as new version' : 'Save item'}</button></div>
        </div>
      ) : (
        <>
          <Markdown>{it.stem}</Markdown>
          <ol type="A">{it.options.map((o, i) => <li key={i} className={i === it.answer_index ? 'text-success' : undefined}>{o}{i === it.answer_index && <strong> <Check className="icon-inline" aria-hidden="true" /> Correct answer</strong>}</li>)}</ol>
          <details><summary className="small">Explanation &amp; hints</summary><p className="small">{it.explanation}</p><ul className="small">{it.hints.map((h, i) => <li key={i}>{h}</li>)}</ul></details>
        </>
      )}
      {it.review_note && <p className="small muted">Note: {it.review_note}</p>}
      <Alert>{act.error}</Alert>
      {it.status === 'pending' && !editing && (
        <div className="card-actions">
          <button className="danger small" disabled={act.busy} onClick={async () => {
            if (await askConfirm({ title: 'Delete this question?', message: `This draft ${TYPE_LABEL[it.purpose] ?? it.purpose} question for ${it.clo_code} will be removed and will not be part of the questionnaire. No student has seen it.`, confirmLabel: 'Delete question', danger: true })
              && (await act.run(() => api(`/items/${it.id}`, { method: 'DELETE' }), 'Question deleted'))) onDone()
          }}><Trash2 aria-hidden="true" />Delete question</button>
        </div>
      )}
    </article>
  )
}

export default function ItemBankPage() {
  const [params, setParams] = useSearchParams()
  const courseId = params.get('course') ?? ''
  const [purpose, setPurpose] = useState('diagnostic')
  const [status, setStatus] = useState('pending')
  const courses = useLoad(() => api<{ id: number; code: string; title: string; status: string }[]>('/items/my-courses'))
  const cov = useLoad(() => (courseId ? api<Cov>(`/items/coverage/${courseId}?purpose=${purpose}`) : Promise.resolve(null)), [courseId, purpose])
  // The Drafts view lists the whole questionnaire (every type) so it is reviewed and approved together.
  const items = useLoad(() => (courseId ? api<Item[]>(`/items?course_id=${courseId}${status === 'pending' ? '' : `&purpose=${purpose}`}${status ? `&status=${status}` : ''}`) : Promise.resolve([] as Item[])), [courseId, purpose, status])
  const act = useAction()
  const [count, setCount] = useState(3)
  const reload = () => { cov.reload(); items.reload() }

  return (
    <>
      <h1>Item bank</h1>
      <p className="muted">Generate questions for each CLO from its learning outcome (suggested reading is used when you have added it), then approve them. Diagnostic tests can only be published when every CLO has the minimum number of approved items.</p>
      <AutomationNote what="Test questions" needsApproval />
      <div className="table-tools">
        <label className="inline-field"><span className="inline-field-label">Course</span>
          <select value={courseId} onChange={(e) => setParams({ course: e.target.value })}>
            <option value="">Choose a course…</option>{courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title} ({c.status})</option>)}
          </select></label>
        <label className="inline-field"><span className="inline-field-label">Purpose</span>
          <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
            <option value="diagnostic">Diagnostic</option><option value="formative">Formative quiz</option><option value="reteach">Reteach</option>
          </select></label>
      </div>
      <ScopeHint count={courses.data?.length} />
      <AssignHint />
      <Alert>{act.error}</Alert>
      {act.message && <Alert kind="success">{act.message}</Alert>}
      {cov.error && <Alert onRetry={cov.reload}>{cov.error}</Alert>}
      {!courseId && !courses.loading && <EmptyState icon={ListChecks} title="Choose a course">Pick a course above to see its question coverage and items.</EmptyState>}
      {courseId && cov.data && (
        <div className="card">
          <div className="spread">
            <h2 className="m-0">Coverage</h2>
            <div className="row">
              <label htmlFor="gc" className="m-0">Items per CLO</label>
              <input id="gc" className="input-narrow" type="number" min={1} max={10} value={count} onChange={(e) => setCount(Number(e.target.value))} />
              <button className={purpose === 'diagnostic' ? 'secondary' : undefined} disabled={act.busy} aria-busy={act.busy || undefined} onClick={async () => {
                const r = await act.run(() => api<{ created: unknown[]; problems: string[]; origins: string[] }>('/items/generate', { body: { course_id: Number(courseId), purpose, count } }))
                if (r) { act.setMessage(`Generated ${r.created.length} items (${r.origins.join(', ') || 'none'}) — review them below.${r.problems.length ? ' ' + r.problems.join(' ') : ''}`); reload() }
              }}>{act.busy ? 'Generating… (one AI call per outcome, up to a minute)' : 'Generate with AI'}</button>
              {purpose === 'diagnostic' && (
                <button disabled={!cov.data.complete || cov.data.published} onClick={async () => { if (await act.run(() => api(`/diagnostic/publish/${courseId}`, { method: 'POST' }), 'Diagnostic published')) reload() }}>{cov.data.published ? 'Published' : 'Publish diagnostic'}</button>
              )}
            </div>
          </div>
          {purpose === 'diagnostic' && (
            <p className="small">Minimum approved per CLO: <strong>{cov.data.min_items_per_clo}</strong> · {cov.data.complete ? <Badge value="coverage complete" tone="ok" /> : <Badge value={`short: ${cov.data.short.join(', ')}`} tone="bad" />}</p>
          )}
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
            <thead><tr><th>CLO</th><th className="num">Approved</th><th className="num">Pending</th><th>Difficulties</th></tr></thead>
            <tbody>{cov.data.clos.map((r) => (
              <tr key={r.clo_id}><td><strong>{r.clo_code}</strong> <span className="small muted">{r.description}</span></td>
                <td className="num"><Badge value={purpose === 'diagnostic' ? `${r.approved} — ${r.meets_minimum ? 'enough' : 'too few'}` : r.approved} tone={purpose !== 'diagnostic' ? '' : r.meets_minimum ? 'ok' : 'bad'} /></td><td className="num">{r.pending}</td><td>{r.difficulties.join(', ') || '—'}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      {courseId && (
        <>
          <div className="tabs" role="group" aria-label="Filter by status">{['pending', 'approved', 'rejected', 'retired', ''].map((s) => <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{s === 'pending' ? 'Drafts (whole questionnaire)' : s || 'all'}</button>)}</div>
          {items.loading ? <Loading variant="cards" /> : items.error ? <Alert onRetry={items.reload}>{items.error}</Alert> : items.data?.length === 0 ? <EmptyState icon={status === 'pending' ? CheckCircle2 : ListChecks} title={status === 'pending' ? 'No draft questions' : 'No items here'}>{status === 'pending' ? "You're all caught up. Generate more questions with AI above if a CLO is short." : 'Try another status filter.'}</EmptyState> : (
            <>
              {items.data?.map((it) => <ItemCard key={`${it.id}-${it.status}`} it={it} onDone={reload} />)}
              {status === 'pending' && items.data && items.data.length > 0 && <ApproveQuestionnaire courseId={courseId} drafts={items.data} onDone={reload} />}
            </>
          )}
        </>
      )}
    </>
  )
}
