import { CheckCircle2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import AutomationNote from '../../components/AutomationNote'
import { Alert, Badge, EmptyState, Loading, fmtDate, useAction, useLoad } from '../../components/ui'

type Task = { id: number; kind: string; ref_type: string; ref_id: number; course_id: number; course_code: string; title: string; status: string; created_at: string; note: string | null; detail?: { question: string; response: string | null; status: string } }

// Where each kind of review is decided.
export const REVIEW_LINKS: Record<string, string> = {
  diagnostic_item: '/items',
  formative_item: '/items',
  content: '/content',
  mapping: '/mappings',
  content_error: '/content',
  escalation: '/console',
  appeal: '/appeals',
}

/** Basic instructor review interface: every pending AI output or request routed to me. */
export default function ReviewQueuePage() {
  const [status, setStatus] = useState('pending')
  const { data, error, loading, reload } = useLoad(() => api<Task[]>(`/reviews?status=${status}`), [status])
  const act = useAction()
  const [notes, setNotes] = useState<Record<number, string>>({})
  return (
    <>
      <h1>Review queue</h1>
      <p className="muted">AI-generated material and flagged answers for the courses you currently teach. Test questions always need your approval here or in the Item bank. With automation off, lessons need approval too; with it on, lessons are already live and this queue is for checking them afterwards.</p>
      <AutomationNote what="Lessons and personal lectures" />
      <div className="tabs" role="group" aria-label="Filter by status">
        {['pending', 'approved', 'rejected', 'resolved'].map((s) => <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}>{s}</button>)}
      </div>
      <Alert>{error || act.error}</Alert>
      {loading ? <Loading variant="table" /> : data?.length === 0 ? <EmptyState icon={CheckCircle2} title={status === 'pending' ? 'No pending reviews' : 'Nothing here'}>{status === 'pending' ? "You're all caught up." : 'Try another status filter.'}</EmptyState> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)">
          <table>
            <thead><tr><th>Item</th><th>Course</th><th>Type</th><th>Created</th><th>Action</th></tr></thead>
            <tbody>{data?.map((t) => (
              <tr key={t.id}>
                <td>{t.title}{t.detail && <div className="small"><div><strong>Q:</strong> {t.detail.question}</div><div><strong>A:</strong> {t.detail.response}</div></div>}{t.note && <div className="small muted">Note: {t.note}</div>}</td>
                <td>{t.course_code}</td>
                <td><Badge value={t.kind} /></td>
                <td className="small">{fmtDate(t.created_at)}</td>
                <td className="min-w-md">
                  {t.status !== 'pending' ? <Badge value={t.status} /> : t.kind === 'tutor_answer' ? (
                    <>
                      <label className="inline-field"><span className="inline-field-label">Resolution note</span><input placeholder="What did you do?" value={notes[t.id] ?? ''} onChange={(e) => setNotes({ ...notes, [t.id]: e.target.value })} /></label>
                      <button className="secondary small mt-2" onClick={async () => { if (await act.run(() => api(`/reviews/${t.id}/resolve`, { body: { note: notes[t.id] ?? '' } }))) reload() }}>Resolve</button>
                    </>
                  ) : <Link className="btn secondary small" to={`${REVIEW_LINKS[t.kind] ?? '/'}?course=${t.course_id}`}>Open</Link>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  )
}
