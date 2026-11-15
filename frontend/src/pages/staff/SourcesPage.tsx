import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { askText } from '../../components/dialogApi'
import { useAuth } from '../../auth/AuthContext'
import { Alert, Badge, fmtDate, Loading, useAction, useLoad } from '../../components/ui'

type Src = { id: number; course_code: string; title: string; kind: string; filename: string; version_no: number; status: string; uploaded_by: string; uploaded_by_id: number; uploaded_at: string; reviewed_by: string | null; review_note: string | null; chunks: number; license_note: string | null }
type Hit = { source_title: string; heading: string | null; text: string; score: number }

export function useCourses() {
  return useLoad(() => api<{ id: number; code: string; title: string; status: string }[]>('/outcomes/courses'))
}

export default function SourcesPage() {
  const { user } = useAuth()
  const courses = useCourses()
  const [courseId, setCourseId] = useState('')
  const list = useLoad(() => (courseId ? api<Src[]>(`/sources?course_id=${courseId}`) : api<Src[]>('/sources')), [courseId])
  const act = useAction()
  const [form, setForm] = useState({ title: '', kind: 'lecture_notes', license_note: '', replaces_id: '' })
  const [file, setFile] = useState<File | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[] | null>(null)
  const usable = (courses.data ?? []).filter((c) => c.status !== 'superseded' && c.status !== 'draft')

  async function upload(e: FormEvent) {
    e.preventDefault()
    if (!file || !courseId) return
    const fd = new FormData()
    fd.append('course_id', courseId)
    fd.append('title', form.title)
    fd.append('kind', form.kind)
    if (form.license_note) fd.append('license_note', form.license_note)
    if (form.replaces_id) fd.append('replaces_id', form.replaces_id)
    fd.append('file', file)
    if (await act.run(() => api('/sources/upload', { form: fd }), 'Uploaded — waiting for another reviewer to approve')) list.reload()
  }
  async function review(id: number, decision: string) {
    if (await act.run(() => api(`/sources/${id}/review`, { body: { decision, note: notes[id] ?? '' } }), `Source ${decision}d`)) list.reload()
  }

  return (
    <>
      <h1>Suggested reading</h1>
      <p className="muted">Optional. The AI already works from each course’s CLOs and PLOs. If you want, add textbooks, notes or slides as suggested reading: once a different reviewer approves a source, the AI also uses it (and cites it) in questions, lessons, hints and tutor answers.</p>
      <div className="field max-w-md">
        <label htmlFor="sc">Course</label>
        <select id="sc" value={courseId} onChange={(e) => { setCourseId(e.target.value); setHits(null) }}>
          <option value="">All my courses</option>{usable.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
        </select>
      </div>
      <Alert>{act.error}</Alert>
      {list.error && <Alert onRetry={list.reload}>{list.error}</Alert>}

      {courseId && (
        <div className="grid grid-wide">
          <form className="card" onSubmit={upload}>
            <h2 className="mt-0">Add a source</h2>
            <div className="field"><label htmlFor="st">Title</label><input id="st" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
            <div className="row">
              <div className="field flex-1"><label htmlFor="sk">Type</label>
                <select id="sk" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                  {['textbook', 'lecture_notes', 'slides', 'reference', 'other'].map((k) => <option key={k}>{k}</option>)}
                </select></div>
              <div className="field flex-1"><label htmlFor="sr">Replaces (new version of)</label>
                <select id="sr" value={form.replaces_id} onChange={(e) => setForm({ ...form, replaces_id: e.target.value })}>
                  <option value="">— new source —</option>{list.data?.filter((s) => s.status === 'approved').map((s) => <option key={s.id} value={s.id}>{s.title} v{s.version_no}</option>)}
                </select></div>
            </div>
            <div className="field"><label htmlFor="sl">Licence / permission note</label><input id="sl" value={form.license_note} onChange={(e) => setForm({ ...form, license_note: e.target.value })} placeholder="e.g. Department-owned notes" /></div>
            <div className="field"><label htmlFor="sf">File</label><input id="sf" type="file" accept=".pdf,.docx,.pptx,.html,.md,.txt" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></div>
            <button disabled={act.busy}>Upload</button>
          </form>
          <form className="card" onSubmit={async (e) => { e.preventDefault(); const r = await act.run(() => api<Hit[]>(`/sources/search?course_id=${courseId}&q=${encodeURIComponent(q)}`)); if (r) setHits(r) }}>
            <h2 className="mt-0">Grounding preview</h2>
            <p className="small muted">See which approved passages the AI would receive for a question.</p>
            <div className="row items-end"><label className="inline-field flex-1"><span className="inline-field-label">Question</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. what is a base case?" /></label><button>Search</button></div>
            {hits && (hits.length ? hits.map((h, i) => (
              <div key={i} className="small mt-2"><strong>{h.source_title}</strong> · {h.heading} <span className="muted">(score {h.score})</span><pre className="plain">{h.text}</pre></div>
            )) : <p className="small">No approved passage matches — the AI would refuse to answer.</p>)}
          </form>
        </div>
      )}

      {list.loading ? <Loading variant="table" /> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)">
          <table>
            <thead><tr><th>Source</th><th>Course</th><th>Status</th><th>Uploaded</th><th>Review</th></tr></thead>
            <tbody>
              {list.data?.map((s) => (
                <tr key={s.id}>
                  <td><strong>{s.title}</strong> v{s.version_no}<div className="small muted">{s.kind} · {s.filename} · {s.chunks} passages</div>{s.license_note && <div className="small muted">{s.license_note}</div>}</td>
                  <td>{s.course_code}</td>
                  <td><Badge value={s.status} />{s.review_note && <div className="small muted">{s.review_note}</div>}</td>
                  <td className="small">{s.uploaded_by}<div className="muted">{fmtDate(s.uploaded_at)}</div></td>
                  <td className="min-w-lg">
                    {s.status === 'pending' && (
                      <>
                        {s.uploaded_by_id === user?.id && <p className="small muted">You uploaded this. Another instructor on the course approves it; if you are the only one, you can approve it yourself (recorded in the audit log).</p>}
                        <label className="inline-field"><span className="inline-field-label">Review note</span><input placeholder="Note (required to reject)" value={notes[s.id] ?? ''} onChange={(e) => setNotes({ ...notes, [s.id]: e.target.value })} /></label>
                        <div className="row mt-2"><button onClick={() => review(s.id, 'approve')}>Approve</button><button className="danger" onClick={() => review(s.id, 'reject')}>Reject</button></div>
                      </>
                    )}
                    {s.status === 'approved' && <button className="secondary" onClick={async () => { const r = await askText({ title: 'Retire this source?', message: 'The tutor will stop using this source for answers, hints and new content.', inputLabel: 'Reason for retiring', confirmLabel: 'Retire source', danger: true }); if (r && (await act.run(() => api(`/sources/${s.id}/retire`, { body: { reason: r } }), 'Retired'))) list.reload() }}>Retire</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
