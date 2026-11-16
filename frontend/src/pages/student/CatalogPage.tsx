import { BookPlus, Lock, Map as MapIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { askText } from '../../components/dialogApi'
import { StatusPill } from '../../components/kit/Kit'
import { Alert, Badge, EmptyState, fmtDate, Loading, useAction, useLoad } from '../../components/ui'

/** A section's state for this student, as a status - not an error. */
function SectionState({ problems, courseId }: { problems: string[]; courseId: number }) {
  const enrolled = problems.find((p) => p.startsWith('Already enrolled'))
  const completed = problems.find((p) => p.endsWith('already completed'))
  if (enrolled) return <span className="section-state"><StatusPill status="attained">Enrolled</StatusPill><Link className="kbtn kbtn-secondary kbtn-sm" to={`/learn/${courseId}`}>Open course</Link></span>
  if (completed) return <StatusPill status="attained">Completed</StatusPill>
  const roadmap = problems.find((p) => /semester/i.test(p))
  const other = problems.filter((p) => p !== roadmap)
  return (
    <span className="section-state">
      {roadmap && <span className="muted small"><Lock className="icon-inline" aria-hidden="true" /> {roadmap}</span>}
      {other.length > 0 && <StatusPill status="near">Not available: {other.join('; ')}</StatusPill>}
    </span>
  )
}

type Sec = { id: number; code: string; term: string; capacity: number; seats_left: number; instructors: string[]; problems: string[] }
type Course = { id: number; code: string; title: string; credits: number; semester: number | null; roadmap_problem: string | null; description: string | null; prerequisites: { code: string; met: boolean }[]; sections: Sec[] }
type Enr = { id: number; status: string; course: { code: string; title: string }; section: { code: string }; term: string; enrolled_at: string; withdrawn_at: string | null; withdraw_reason: string | null }

export default function CatalogPage() {
  const cat = useLoad(() => api<Course[]>('/enrollment/catalog'))
  const mine = useLoad(() => api<Enr[]>('/enrollment/mine'))
  const act = useAction()
  const reload = () => { cat.reload(); mine.reload() }

  async function withdraw(id: number) {
    const reason = await askText({
      title: 'Withdraw from this course?',
      message: 'You will be removed from the section and lose access to its learning material. Your seat may be given to another student.',
      inputLabel: 'Why are you withdrawing?',
      confirmLabel: 'Withdraw from course',
      danger: true,
    })
    if (reason && (await act.run(() => api(`/enrollment/${id}/withdraw`, { body: { reason } }), 'Withdrawn'))) reload()
  }

  return (
    <>
      <div className="phead">
        <div><h1>Courses &amp; enrolment</h1><p className="phead-desc">See your enrolments and join an open section.</p></div>
        <div className="phead-actions"><Link className="kbtn kbtn-secondary kbtn-md" to="/roadmap"><MapIcon aria-hidden="true" />See my semester roadmap</Link></div>
      </div>
      <Alert>{act.error}</Alert>
      {(cat.error || mine.error) && <Alert onRetry={reload}>{cat.error || mine.error}</Alert>}
      <h2>My enrolments</h2>
      {mine.loading ? <Loading variant="table" /> : mine.data?.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
          <thead><tr><th>Course</th><th>Section</th><th>Status</th><th>Dates</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{mine.data.map((e) => (
            <tr key={e.id}>
              <td><strong>{e.course.code}</strong> {e.course.title}</td><td>{e.term} · {e.section.code}</td>
              <td><Badge value={e.status} />{e.withdraw_reason && <div className="small muted">{e.withdraw_reason}</div>}</td>
              <td className="small">{fmtDate(e.enrolled_at)}{e.withdrawn_at && ` → ${fmtDate(e.withdrawn_at)}`}</td>
              <td>{e.status === 'enrolled' && <button className="danger small" onClick={() => withdraw(e.id)}>Withdraw<span className="sr-only"> from {e.course.code}</span></button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      ) : !mine.error && <EmptyState icon={BookPlus} title="No enrolments yet">Choose a section under Available courses to enrol.</EmptyState>}

      <h2>Available courses</h2>
      <p className="small muted">You can enrol in courses of your current roadmap semester and of earlier semesters you have not finished.</p>
      {!cat.loading && !cat.error && cat.data?.length === 0 && <EmptyState icon={BookPlus} title="No courses open right now">Check back later or contact your programme office.</EmptyState>}
      {cat.loading ? <Loading variant="cards" /> : [...(cat.data ?? [])].sort((a, b) => (a.semester ?? 99) - (b.semester ?? 99) || a.code.localeCompare(b.code)).map((c) => (
        <div className="card" key={c.id}>
          <div className="spread"><h3 className="m-0">{c.code} — {c.title}</h3><span className="row">{c.semester && <Badge value={`Semester ${c.semester}`} tone={c.roadmap_problem ? '' : 'info'} />}<span className="small muted">{c.credits} credits</span></span></div>
          {c.roadmap_problem && <p className="small muted"><Lock className="icon-inline" aria-hidden="true" /> Opens later in your roadmap: {c.roadmap_problem}</p>}
          {c.description && <p className="card-desc">{c.description}</p>}
          <p className="small">Prerequisites: {c.prerequisites.length ? c.prerequisites.map((p) => <Badge key={p.code} value={`${p.code} — ${p.met ? 'met' : 'not met'}`} tone={p.met ? 'ok' : 'bad'} />) : 'none'}</p>
          {c.sections.length === 0 && <p className="small muted">No open sections.</p>}
          {c.sections.map((s) => (
            <div key={s.id} className="row divided">
              <span>{s.term} · section {s.code}</span>
              <span className="small muted">{s.instructors.join(', ') || 'instructor TBA'} · {s.seats_left}/{s.capacity} seats left</span>
              <span className="flex-1" />
              {s.problems.length ? <SectionState problems={s.problems} courseId={c.id} /> : (
                <button disabled={act.busy} onClick={async () => { if (await act.run(() => api('/enrollment', { body: { section_id: s.id } }), `Enrolled in ${c.code}`)) reload() }}>Enrol<span className="sr-only"> in {c.code} section {s.code}</span></button>
              )}
            </div>
          ))}
        </div>
      ))}
    </>
  )
}
