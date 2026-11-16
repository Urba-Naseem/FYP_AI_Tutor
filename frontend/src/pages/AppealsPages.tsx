import { Bell, CheckCircle2, Scale } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import Markdown from '../components/Markdown'
import { Alert, Badge, EmptyState, Loading, fmtDate, useAction, useLoad } from '../components/ui'
import type { Attempt } from './student/QuizPage'

type Appeal = { id: number; student: { name: string }; course: { id: number; code: string }; clo_code: string; quiz_attempt_id: number; item_id: number | null; reason: string; status: string; original_score: string; amended_score: string | null; resolution: string | null; reviewer: string | null; created_at: string; resolved_at: string | null; attempt?: Attempt }
type Eligible = { quiz_attempt_id: number; clo_code: string; kind: string; completed_at: string; score: string; item_ids: number[]; has_open_appeal: boolean }

/** Student: appeal a quiz result and follow the outcome. */
export function StudentAppealsPage() {
  const courses = useLoad(() => api<{ id: number; code: string; title: string }[]>('/diagnostic/courses/mine'))
  const [courseId, setCourseId] = useState('')
  const elig = useLoad(() => (courseId ? api<Eligible[]>(`/appeals/eligible/${courseId}`) : Promise.resolve([] as Eligible[])), [courseId])
  const mine = useLoad(() => api<Appeal[]>('/appeals/mine'))
  const [form, setForm] = useState({ attempt: '', item: '', reason: '' })
  const act = useAction()
  const sel = elig.data?.find((e) => String(e.quiz_attempt_id) === form.attempt)

  return (
    <>
      <h1>Assessment appeals</h1>
      <p className="muted">If you believe a quiz result is wrong (for example a question had two correct answers), you can appeal within 14 days. Your instructor reviews it and you will be notified of the outcome.</p>
      <div className="card">
        <h2 className="mt-0">New appeal</h2>
        <div className="grid">
          <div className="field"><label htmlFor="ac">Course</label>
            <select id="ac" value={courseId} onChange={(e) => { setCourseId(e.target.value); setForm({ attempt: '', item: '', reason: '' }) }}>
              <option value="">Choose…</option>{courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
            </select></div>
          <div className="field"><label htmlFor="aa">Quiz attempt</label>
            <select id="aa" value={form.attempt} onChange={(e) => setForm({ ...form, attempt: e.target.value, item: '' })}>
              <option value="">Choose…</option>
              {elig.data?.filter((e) => !e.has_open_appeal).map((e) => <option key={e.quiz_attempt_id} value={e.quiz_attempt_id}>{e.clo_code} {e.kind} · {e.score} · {fmtDate(e.completed_at)}</option>)}
            </select></div>
          {sel && <div className="field"><label htmlFor="ai">Question (optional)</label>
            <select id="ai" value={form.item} onChange={(e) => setForm({ ...form, item: e.target.value })}>
              <option value="">Whole attempt</option>{sel.item_ids.map((id, i) => <option key={id} value={id}>Question {i + 1}</option>)}
            </select></div>}
        </div>
        <div className="field"><label htmlFor="ar">Why do you think the result is wrong?</label><textarea id="ar" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></div>
        <Alert>{act.error}</Alert>
        <button disabled={!form.attempt || form.reason.trim().length < 15} onClick={async () => {
          if (await act.run(() => api('/appeals', { body: { quiz_attempt_id: Number(form.attempt), item_id: form.item ? Number(form.item) : null, reason: form.reason } }), 'Appeal submitted to your instructor')) {
            setForm({ attempt: '', item: '', reason: '' }); mine.reload(); elig.reload()
          }
        }}>Submit appeal</button>
      </div>
      <h2>My appeals</h2>
      {mine.loading ? <Loading variant="table" /> : mine.data?.length === 0 ? <EmptyState icon={Scale} title="No appeals yet">If you think an assessment result is wrong, submit an appeal above.</EmptyState> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
          <thead><tr><th>Course / outcome</th><th>Status</th><th>Score</th><th>Decision</th></tr></thead>
          <tbody>{mine.data?.map((a) => (
            <tr key={a.id}>
              <td>{a.course.code} {a.clo_code}<div className="small muted">{fmtDate(a.created_at)}: {a.reason}</div></td>
              <td><Badge value={a.status} /></td>
              <td>{a.original_score}{a.amended_score && <> → <strong>{a.amended_score}</strong></>}</td>
              <td className="small">{a.resolution ?? '—'}{a.reviewer && <div className="muted">by {a.reviewer}</div>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  )
}

function AppealReview({ id, onDone }: { id: number; onDone: () => void }) {
  const d = useLoad(() => api<Appeal>(`/appeals/${id}`), [id])
  const act = useAction()
  const [f, setF] = useState({ decision: 'rejected', resolution: '', new_correct: '' })
  if (!d.data) return <Loading />
  const a = d.data
  const total = a.attempt?.total ?? 0
  return (
    <div className="card">
      <div className="spread"><h2 className="m-0">Appeal #{a.id} — {a.student.name}</h2><Badge value={a.status} /></div>
      <p><strong>{a.course.code} {a.clo_code}</strong> · score {a.original_score} · {fmtDate(a.created_at)}</p>
      <Alert kind="info">{a.reason}</Alert>
      {a.attempt?.questions.map((q, i) => (
        <details key={q.id} open={q.id === a.item_id} className="card">
          <summary>Question {i + 1} {q.id === a.item_id && <Badge value="appealed" tone="warn" />} {q.answer && <Badge value={q.answer.correct ? 'correct' : 'wrong'} tone={q.answer.correct ? 'ok' : 'bad'} />}</summary>
          <Markdown>{q.stem}</Markdown>
          <ol type="A">{q.options.map((o, k) => <li key={k} className={q.answer?.correct_index === k ? 'text-success' : undefined}>{o}{q.answer?.correct_index === k && <strong> — answer key</strong>}{q.answer?.chosen === k && <em> — student's answer</em>}</li>)}</ol>
        </details>
      ))}
      {['submitted', 'under_review'].includes(a.status) && (
        <>
          {a.status === 'submitted' && <button className="secondary" onClick={async () => { if (await act.run(() => api(`/appeals/${id}/start-review`, { method: 'POST' }))) d.reload() }}>Mark as under review (notifies student)</button>}
          <div className="grid mt-2">
            <div className="field"><label htmlFor="rd">Decision</label><select id="rd" value={f.decision} onChange={(e) => setF({ ...f, decision: e.target.value })}><option value="rejected">Reject — result stands</option><option value="upheld">Uphold — amend the grade</option></select></div>
            {f.decision === 'upheld' && <div className="field"><label htmlFor="rn">Amended correct answers (of {total})</label><input id="rn" type="number" min={0} max={total} value={f.new_correct} onChange={(e) => setF({ ...f, new_correct: e.target.value })} /></div>}
          </div>
          <div className="field"><label htmlFor="rr">Explanation to the student</label><textarea id="rr" value={f.resolution} onChange={(e) => setF({ ...f, resolution: e.target.value })} /></div>
          <Alert>{act.error}</Alert>
          <button disabled={f.resolution.trim().length < 10} onClick={async () => {
            const body = { decision: f.decision, resolution: f.resolution, new_correct: f.new_correct === '' ? null : Number(f.new_correct) }
            if (await act.run(() => api(`/appeals/${id}/resolve`, { body }))) { d.reload(); onDone() }
          }}>Resolve appeal</button>
        </>
      )}
      {a.resolution && <p><strong>Decision:</strong> {a.resolution} {a.amended_score && `(amended to ${a.amended_score})`}</p>}
    </div>
  )
}

/** Instructor: review and resolve appeals for my courses. */
export function AppealReviewPage() {
  const [status, setStatus] = useState('')
  const list = useLoad(() => api<Appeal[]>(`/appeals${status ? `?status=${status}` : ''}`), [status])
  const [open, setOpen] = useState<number | null>(null)
  return (
    <>
      <h1>Appeals</h1>
      <p className="muted">Resolve student appeals. Upholding an appeal creates a new version of the result (the original is kept), recalculates mastery and notifies the student.</p>
      <div className="tabs" role="group" aria-label="Filter by status">{['', 'submitted', 'under_review', 'upheld', 'rejected'].map((s) => <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{s ? s.replace('_', ' ') : 'all'}</button>)}</div>
      {list.loading ? <Loading variant="table" /> : list.data?.length === 0 ? <EmptyState icon={CheckCircle2} title="No appeals here">Try another status filter.</EmptyState> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
          <thead><tr><th>Student</th><th>Course</th><th>Status</th><th>Submitted</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{list.data?.map((a) => (
            <tr key={a.id}><td>{a.student.name}</td><td>{a.course.code} {a.clo_code}</td><td><Badge value={a.status} /></td><td className="small">{fmtDate(a.created_at)}</td>
              <td><button className="secondary" onClick={() => setOpen(a.id)}>Open</button></td></tr>
          ))}</tbody>
        </table></div>
      )}
      {open && <AppealReview id={open} onDone={list.reload} />}
    </>
  )
}

export function NotificationsPage() {
  const n = useLoad(() => api<{ id: number; title: string; body: string; link: string | null; created_at: string; read: boolean }[]>('/notifications'))
  return (
    <>
      <h1>Notifications</h1>
      {n.loading ? <Loading variant="table" /> : n.data?.length === 0 ? <EmptyState icon={Bell} title="No notifications yet">Updates about reviews, appeals and your courses will appear here.</EmptyState> : n.data?.map((x) => (
        <div key={x.id} className={`card${x.read ? " notice-read" : ""}`}>
          <div className="spread"><strong>{x.title}</strong><span className="small muted">{fmtDate(x.created_at)} {!x.read && <Badge value="new" tone="ok" />}</span></div>
          <p className="small">{x.body}</p>
          <div className="row">
            {x.link && <Link to={x.link}>Open</Link>}
            {!x.read && <button className="secondary small" onClick={async () => { await api(`/notifications/${x.id}/read`, { method: 'POST' }); n.reload() }}>Mark as read</button>}
          </div>
        </div>
      ))}
    </>
  )
}
