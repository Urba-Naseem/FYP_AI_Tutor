import { FileText, Pencil, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { askText } from '../../components/dialogApi'
import Markdown from '../../components/Markdown'
import AutomationNote from '../../components/AutomationNote'
import ScopeHint, { AssignHint } from '../../components/ScopeHint'
import { Alert, Badge, EmptyState, fmtDate, Loading, useAction, useLoad } from '../../components/ui'
import ContentReports from './ContentReports'

type Course = { id: number; code: string; title: string; units: { id: number; number: number; title: string }[]; clos: { id: number; code: string; unit_id: number | null; description: string }[] }
type Ver = { id: number; version_no: number; status: string; origin: string; change_summary: string | null; created_at: string; review_note: string | null; body_md: string }
type Doc = { id: number; unit_id: number; clo_id: number | null; clo_code: string | null; kind: string; title: string; approved_version: number | null; latest_version: number | null; latest_status: string | null; pending: number; versions?: Ver[] }

function DocPanel({ docId, onChange }: { docId: number; onChange: () => void }) {
  const d = useLoad(() => api<Doc>(`/content/${docId}`), [docId])
  const [sel, setSel] = useState<number | null>(null)
  const [edit, setEdit] = useState<string | null>(null)
  const [summary, setSummary] = useState('')
  const [note, setNote] = useState('')
  const [diff, setDiff] = useState('')
  const act = useAction()
  if (d.loading && !d.data) return <Loading variant="cards" />
  if (!d.data?.versions) return <Alert onRetry={d.reload}>{d.error}</Alert>
  const v = d.data.versions.find((x) => x.id === sel) ?? d.data.versions[0]
  const published = d.data.versions.find((x) => x.version_no === d.data!.approved_version && x.id !== v.id)
  const refresh = () => { d.reload(); onChange() }
  async function reject() {
    const reason = await askText({ title: `Reject v${v.version_no}?`, message: 'The draft will not be published. Your reason is saved with the version so the author can fix it.', inputLabel: 'Reason for rejecting', confirmLabel: 'Reject version', danger: true })
    if (reason && (await act.run(() => api(`/content/versions/${v.id}/review`, { body: { decision: 'reject', note: reason } }), 'Version rejected'))) refresh()
  }
  return (
    <section className="card" aria-labelledby="doc-title">
      <div className="spread"><h2 id="doc-title" className="m-0">{d.data.title}</h2>
        <div className="row">
          <label className="inline-field"><span className="inline-field-label">Version</span>
            <select value={v.id} onChange={(e) => { setSel(Number(e.target.value)); setDiff('') }}>
              {d.data.versions.map((x) => <option key={x.id} value={x.id}>v{x.version_no} — {x.status}</option>)}
            </select></label>
          {edit === null && <button className="secondary" onClick={() => setEdit(v.body_md)}><Pencil aria-hidden="true" />Edit as new version</button>}
        </div>
      </div>
      <p className="mt-3"><Badge value={`v${v.version_no}`} /> <Badge value={v.status} /></p>
      <p className="small muted">{v.origin} · {v.change_summary || 'No change summary'} · {fmtDate(v.created_at)}{v.review_note && ` · note: ${v.review_note}`}</p>
      <Alert>{act.error}</Alert>
      {edit !== null ? (
        <div className="editor-grid">
          <div><label htmlFor="cs-md">Lesson (Markdown)</label><textarea id="cs-md" className="code-input" value={edit} onChange={(e) => setEdit(e.target.value)} />
            <label className="inline-field mt-2"><span className="inline-field-label">Change summary</span><input placeholder="What changed?" value={summary} onChange={(e) => setSummary(e.target.value)} /></label>
            <div className="row mt-2">
              <button onClick={async () => { if (await act.run(() => api(`/content/${docId}/versions`, { body: { body_md: edit, change_summary: summary } }))) { setEdit(null); setSel(null); refresh() } }}>Save new version</button>
              <button className="secondary" onClick={() => setEdit(null)}>Cancel</button>
            </div></div>
          <div><p className="field-title">Preview</p><div className="card preview-pane"><Markdown>{edit}</Markdown></div></div>
        </div>
      ) : (
        <>
          {d.data.versions.length > 1 && v.version_no > 1 && (
            <button className="ghost small" onClick={async () => { const prev = d.data!.versions!.find((x) => x.version_no === v.version_no - 1); if (prev) setDiff((await api<{ diff: string }>(`/content/versions/${prev.id}/diff/${v.id}`)).diff) }}>Compare with previous version</button>
          )}
          {diff && <pre className="plain" tabIndex={0}>{diff}</pre>}
          {v.status === 'pending' ? (
            <>
              <div className="editor-grid mt-4">
                <div>
                  <p className="field-title">{published ? `Published version (v${published.version_no})` : 'Published version'}</p>
                  <div className="card preview-pane">{published ? <Markdown>{published.body_md}</Markdown> : <p className="muted m-0">Nothing is published yet — this draft would be the first version students see.</p>}</div>
                </div>
                <div>
                  <p className="field-title">Draft v{v.version_no} · Pending review</p>
                  <div className="card preview-pane"><Markdown>{v.body_md}</Markdown></div>
                </div>
              </div>
              <div className="action-bar">
                <label className="inline-field flex-1"><span className="inline-field-label">Review note (optional when approving)</span><input value={note} onChange={(e) => setNote(e.target.value)} /></label>
                <div className="row">
                  <button className="danger" disabled={act.busy} onClick={reject}>Reject</button>
                  <button disabled={act.busy} aria-busy={act.busy || undefined} onClick={async () => { if (await act.run(() => api(`/content/versions/${v.id}/review`, { body: { decision: 'approve', note } }), 'Version approved and published')) refresh() }}>Approve and publish</button>
                </div>
              </div>
            </>
          ) : <div className="card preview-pane mt-4"><Markdown>{v.body_md}</Markdown></div>}
        </>
      )}
    </section>
  )
}

export default function ContentStudioPage() {
  const [params, setParams] = useSearchParams()
  const courseId = params.get('course') ?? ''
  const courses = useLoad(() => api<{ id: number; code: string; title: string; status: string }[]>('/items/my-courses'))
  const course = useLoad(() => (courseId ? api<Course>(`/outcomes/courses/${courseId}`) : Promise.resolve(null)), [courseId])
  const docs = useLoad(() => (courseId ? api<Doc[]>(`/content?course_id=${courseId}`) : Promise.resolve([] as Doc[])), [courseId])
  const [open, setOpen] = useState<number | null>(null)
  const act = useAction()

  async function draft(unit_id: number, kind = 'lesson', clo_id?: number) {
    const r = await act.run(() => api<Doc>('/content/draft', { body: { course_id: Number(courseId), unit_id, kind, clo_id } }), 'Draft created — review it below')
    if (r) { docs.reload(); setOpen(r.id) }
  }

  return (
    <>
      <h1>Learning content</h1>
      <p className="muted">Draft lessons and alternate explanations from each unit’s learning outcomes (suggested reading is used when you have added it), edit them, and approve a version to publish it to students. Every version is kept.</p>
      <AutomationNote what="Lesson drafts and each student’s personal lectures" />
      <div className="field">
        <label htmlFor="cs-course">Course</label>
        <select id="cs-course" value={courseId} onChange={(e) => { setParams({ course: e.target.value }); setOpen(null) }}>
          <option value="">Choose a course…</option>{courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title} ({c.status})</option>)}
        </select>
      </div>
      <ScopeHint count={courses.data?.length} />
      <AssignHint />
      <Alert>{act.error || docs.error}</Alert>
      {!courseId && !courses.loading && <EmptyState icon={FileText} title="Choose a course">Pick a course above to see its units, lessons and alternate explanations.</EmptyState>}
      {courseId && course.loading && <Loading variant="cards" />}
      {courseId && <ContentReports courseId={courseId} />}
      {course.data && course.data.units.map((u) => {
        const lesson = docs.data?.find((d) => d.unit_id === u.id && d.kind === 'lesson')
        return (
          <div className="card" key={u.id}>
            <div className="spread">
              <h3 className="m-0">Unit {u.number}: {u.title}</h3>
              <div className="row">
                {lesson ? <>
                  <Badge value={lesson.approved_version ? `published v${lesson.approved_version}` : 'not published'} tone={lesson.approved_version ? 'ok' : 'warn'} />
                  {lesson.pending > 0 && <Badge value={`${lesson.pending} pending`} />}
                  <button onClick={() => setOpen(lesson.id)}>Open lesson<span className="sr-only"> for unit {u.number}</span></button>
                </> : null}
                <button className={lesson ? 'secondary' : undefined} disabled={act.busy} aria-busy={act.busy || undefined} onClick={() => draft(u.id)}><Sparkles aria-hidden="true" />{lesson ? 'New AI draft' : 'Draft lesson with AI'}</button>
              </div>
            </div>
            <div className="small mt-2">
              Alternate explanations (used when reteaching):{' '}
              {course.data!.clos.filter((o) => o.unit_id === u.id).map((o) => {
                const alt = docs.data?.find((d) => d.kind === 'alternate' && d.clo_id === o.id)
                return (
                  <span key={o.id} className="row inline-row">
                    <strong>{o.code}</strong>
                    {alt ? <button className="secondary small" onClick={() => setOpen(alt.id)}>{alt.approved_version ? `Open v${alt.approved_version} (published)` : 'Open draft'}<span className="sr-only"> for {o.code}</span></button>
                      : <button className="secondary small" disabled={act.busy} onClick={() => draft(u.id, 'alternate', o.id)}>Draft with AI<span className="sr-only"> for {o.code}</span></button>}
                  </span>
                )
              })}
            </div>
          </div>
        )
      })}
      {open && <DocPanel docId={open} onChange={docs.reload} />}
    </>
  )
}
