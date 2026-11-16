import { AlertTriangle, BarChart3, CheckCircle2, Gauge, Grid3x3, Inbox, LayoutDashboard, Lock, Users } from 'lucide-react'
import type { ComponentType } from 'react'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { StatTile } from '../../components/charts'
import { Alert, Badge, Bar, Crumbs, EmptyState, fmtDate, Loading, useLoad } from '../../components/ui'
import { PathDecisions, PathSteps, type Path } from '../student/PathPage'

type Att = { clo_id: number; clo_code: string; description: string; threshold: number; assessed: number; attained: number; attained_pct: number | null; avg_mastery: number | null }
type Flag = { type: string; clo_code: string | null; message: string; escalation_id?: number }
type Need = { student_id: number; name: string; student_number: string | null; flags: Flag[]; priority: string }
type Summary = { course: { id: number; code: string; title: string; status: string }; enrolled: number; diagnostic_completed: number; attainment: Att[]; pending_reviews: Record<string, number>; pending_total: number }

export function AttainmentTable({ rows }: { rows: Att[] }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
      <thead><tr><th>CLO</th><th>Students mastered</th><th>Average mastery</th></tr></thead>
      <tbody>{rows.map((a) => (
        <tr key={a.clo_id}>
          <td><strong>{a.clo_code}</strong> <span className="small muted">{a.description}</span></td>
          <td className="min-w-md">{a.attained_pct === null ? <span className="muted">Not assessed yet</span> : <><Bar value={a.attained_pct} label={`${a.clo_code} share of students who mastered it`} /><span className="small muted">{a.attained} of {a.assessed} students</span></>}</td>
          <td className="min-w-md">{a.avg_mastery === null ? '—' : <Bar value={a.avg_mastery} threshold={a.threshold} label={`${a.clo_code} average mastery`} />}</td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}

const avg = (xs: (number | null)[]) => {
  const v = xs.filter((x): x is number => x !== null)
  return v.length ? Math.round(v.reduce((t, x) => t + x, 0) / v.length) : null
}
const KIND_LABEL = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

/** Summary cards (DESIGN.md §6 instructor console). */
function SummaryCards({ enrolled, mastery, pending, flagged, extra }: { enrolled: number; mastery: number | null; pending: number; flagged: number; extra?: string }) {
  return (
    <div className="tiles">
      <StatTile icon={Users} label="Students enrolled" value={enrolled} />
      <StatTile icon={Gauge} tone="ok" label="Average mastery" value={mastery === null ? '—' : `${mastery}%`} sub={mastery === null ? 'no assessments yet' : 'across assessed outcomes'} />
      <StatTile icon={Inbox} tone="info" label="Pending reviews" value={pending} sub={pending ? 'waiting for a decision' : 'all caught up'} />
      <StatTile icon={AlertTriangle} tone={flagged ? 'bad' : 'ok'} label="Flagged learners" value={flagged} sub={extra} />
    </div>
  )
}

function ReviewQueuePanel({ rows }: { rows: { course: string; kind: string; n: number }[] }) {
  return (
    <section className="card" aria-labelledby="rq-title">
      <div className="spread"><h2 id="rq-title" className="m-0">Review queue</h2><Link to="/reviews">Open review queue</Link></div>
      {rows.length === 0 ? <EmptyState icon={CheckCircle2} title="No pending reviews">You're all caught up.</EmptyState> : (
        <div className="table-wrap mt-4" tabIndex={0} role="region" aria-label="Review queue"><table>
          <thead><tr><th>Course</th><th>Type</th><th className="num">Waiting</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.course + r.kind}><td>{r.course}</td><td>{KIND_LABEL(r.kind)}</td><td className="num">{r.n}</td></tr>)}</tbody>
        </table></div>
      )}
    </section>
  )
}

export function ConsolePage() {
  const ov = useLoad(() => api<(Summary & { needs_intervention: number; inactive: number })[]>('/console/overview'))
  const data = ov.data ?? []
  const queue = data.flatMap((c) => Object.entries(c.pending_reviews).filter(([, n]) => n > 0).map(([kind, n]) => ({ course: c.course.code, kind, n })))
  return (
    <>
      <h1>Instructor console</h1>
      <p className="muted">Mastery, pending reviews and learners who may need help, for the courses you currently teach.</p>
      {ov.error && <Alert onRetry={ov.reload}>{ov.error}</Alert>}
      {ov.loading ? <Loading variant="cards" /> : !ov.error && data.length === 0 ? (
        <EmptyState icon={LayoutDashboard} title="No courses assigned">An administrator must assign you to a section in Offerings &amp; faculty before courses appear here.</EmptyState>
      ) : data.length > 0 && (
        <>
          <SummaryCards
            enrolled={data.reduce((t, c) => t + c.enrolled, 0)}
            mastery={avg(data.flatMap((c) => c.attainment.map((a) => a.avg_mastery)))}
            pending={data.reduce((t, c) => t + c.pending_total, 0)}
            flagged={data.reduce((t, c) => t + c.needs_intervention, 0)}
            extra={data.some((c) => c.inactive) ? `+ ${data.reduce((t, c) => t + c.inactive, 0)} inactive` : undefined}
          />
          <div className="two-col">
            <ReviewQueuePanel rows={queue} />
            <section className="card" aria-labelledby="help-title">
              <h2 id="help-title" className="mt-0">Learners needing help</h2>
              {data.every((c) => !c.needs_intervention) ? <EmptyState icon={CheckCircle2} title="Nobody is flagged">No learners currently need an intervention.</EmptyState> : (
                <ul className="clo-list">
                  {data.filter((c) => c.needs_intervention).map((c) => (
                    <li key={c.course.id}>
                      <div><strong>{c.course.code}</strong> <span className="small muted">{c.course.title}</span></div>
                      <div><Badge value={`${c.needs_intervention} flagged`} tone="bad" /></div>
                      <div><Link className="btn secondary small" to={`/console/${c.course.id}`}>View learners<span className="sr-only"> in {c.course.code}</span></Link></div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
          <h2>Courses</h2>
          {data.map((c) => (
            <section className="card" key={c.course.id} aria-labelledby={`c-${c.course.id}`}>
              <div className="spread">
                <h3 id={`c-${c.course.id}`} className="m-0">{c.course.code} — {c.course.title} <Badge value={c.course.status} /></h3>
                <Link className="btn secondary small" to={`/console/${c.course.id}`}>Open course<span className="sr-only"> {c.course.code}</span></Link>
              </div>
              <p className="card-desc">{c.enrolled} enrolled · {c.diagnostic_completed} diagnostics done · {c.pending_total} pending reviews</p>
              <AttainmentTable rows={c.attainment} />
            </section>
          ))}
        </>
      )}
    </>
  )
}

type Detail = Summary & { needs_intervention: Need[]; clo_codes: string[]; matrix: { student_id: number; name: string; student_number: string | null; clos: Record<string, { mastery_pct: number; attained: boolean } | null>; path: { version: number; locked: boolean; done: number; total: number } | null }[] }

export function ConsoleCoursePage() {
  const { courseId = '' } = useParams()
  const d = useLoad(() => api<Detail>(`/console/course/${courseId}`), [courseId])
  const crumbs = (label: string) => <Crumbs items={[{ to: '/console', label: 'Instructor console' }, { label }]} />
  if (d.loading && !d.data) return <>{crumbs('Course')}<Loading variant="cards" /></>
  if (!d.data) return <>{crumbs('Course')}<h1>Course</h1><Alert onRetry={d.reload}>{d.error}</Alert></>
  const x = d.data
  const queue = Object.entries(x.pending_reviews).filter(([, n]) => n > 0).map(([kind, n]) => ({ course: x.course.code, kind, n }))
  return (
    <>
      {crumbs(x.course.code)}
      <h1>{x.course.code} — {x.course.title}</h1>
      <SummaryCards enrolled={x.enrolled} mastery={avg(x.attainment.map((a) => a.avg_mastery))} pending={x.pending_total} flagged={x.needs_intervention.length} extra={`${x.diagnostic_completed} diagnostics done`} />
      <div className="two-col">
        <ReviewQueuePanel rows={queue} />
        <section className="card" aria-labelledby="help-title">
          <h2 id="help-title" className="mt-0">Learners needing help</h2>
          {x.needs_intervention.length === 0 ? <EmptyState icon={CheckCircle2} title="Nobody is flagged">No learners in this course currently need an intervention.</EmptyState> : (
            <ul className="needs-list">
              {x.needs_intervention.map((n) => (
                <li key={n.student_id}>
                  <div className="min-w-0"><strong>{n.name}</strong> <span className="small muted">{n.student_number}</span>
                    <ul className="needs-flags">{n.flags.map((f, i) => <li key={i}>{f.message}</li>)}</ul></div>
                  <div className="needs-actions">
                    <Badge value={`${n.priority} priority`} tone={n.priority === 'high' ? 'bad' : 'warn'} />
                    <Link className="btn secondary small" to={`/console/${courseId}/student`} state={{ studentId: n.student_id }}>Intervene<span className="sr-only"> for {n.name}</span></Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <div className="section-head"><h2><BarChart3 className="icon" aria-hidden="true" />CLO mastery</h2></div>
      <AttainmentTable rows={x.attainment} />
      <div className="section-head"><h2><Grid3x3 className="icon" aria-hidden="true" />Students × outcomes</h2></div>
      {x.matrix.length === 0 ? <EmptyState icon={Users} title="No students enrolled">Students appear here once they enrol in a section of this course.</EmptyState> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Students by outcome"><table>
          <thead><tr><th>Student</th>{x.clo_codes.map((c) => <th key={c}>{c}</th>)}<th className="num">Path</th></tr></thead>
          <tbody>{x.matrix.map((r) => (
            <tr key={r.student_id}>
              <td><Link to={`/console/${courseId}/student`} state={{ studentId: r.student_id }}>{r.name}</Link></td>
              {x.clo_codes.map((c) => { const v = r.clos[c]; return <td key={c}>{v ? <Badge value={`${v.mastery_pct}%`} tone={v.attained ? 'ok' : 'bad'} /> : <Badge value="not_started" />}<span className="sr-only">{v ? (v.attained ? ' mastered' : ' needs review') : ''}</span></td> })}
              <td className="num small">{r.path ? <>{r.path.done}/{r.path.total}{r.path.locked && <> <Lock className="icon-inline" aria-label="locked" /></>}</> : '—'}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  )
}

type StudentDetail = {
  student: { id: number; name: string; student_number: string | null; email: string }
  flags: Flag[]
  mastery: { clo_id: number; clo_code: string; description: string; current: { mastery_pct: number; attained: boolean; threshold: number; attempts_counted: number } | null; evidence: { id: number; source: string; correct: number; total: number; score_pct: number; recorded_at: string; status: string; version_no: number }[] }[]
  diagnostic: { clo_code: string; level: string; score_pct: number }[] | null
  path: Path | null
  recent_activity: { at: string; action: string; details: Record<string, unknown> }[]
}

/** Intervention tools register here (US-6.4 escalations, US-4.4 path control). */
export const STUDENT_PANELS: { title: string; Component: ComponentType<{ courseId: string; studentId: string; detail: StudentDetail; reload: () => void }> }[] = []

/** Remembers the selected student for this tab so a refresh still works; the ID never appears in the URL (§6). */
const studentKey = (courseId: string) => `consoleStudent:${courseId}`
function useSelectedStudent(courseId: string) {
  const { state } = useLocation() as { state: { studentId?: number | string } | null }
  const fromState = state?.studentId != null ? String(state.studentId) : ''
  if (fromState) {
    try { sessionStorage.setItem(studentKey(courseId), fromState) } catch { /* storage unavailable */ }
    return fromState
  }
  try { return sessionStorage.getItem(studentKey(courseId)) ?? '' } catch { return '' }
}

/** Old links (e.g. in notifications) carried the ID in the path: move it into navigation state and clean the URL. */
export function LegacyConsoleStudentRedirect() {
  const { courseId = '', studentId = '' } = useParams()
  return <Navigate to={`/console/${courseId}/student`} state={{ studentId }} replace />
}

export function ConsoleStudentPage() {
  const { courseId = '' } = useParams()
  const studentId = useSelectedStudent(courseId)
  const d = useLoad(() => (studentId ? api<StudentDetail>(`/console/course/${courseId}/student/${studentId}`) : Promise.resolve(null)), [courseId, studentId])
  const crumbs = (label: string) => <Crumbs items={[{ to: '/console', label: 'Instructor console' }, { to: `/console/${courseId}`, label: 'Course' }, { label }]} />
  if (!studentId) return (
    <>
      {crumbs('Learner')}
      <h1>Learner details</h1>
      <EmptyState icon={Users} title="No learner selected" action={<Link className="btn secondary" to={`/console/${courseId}`}>Back to the course console</Link>}>Choose a learner from the course console.</EmptyState>
    </>
  )
  if (d.loading && !d.data) return <>{crumbs('Learner')}<Loading variant="cards" /></>
  if (!d.data) return <>{crumbs('Learner')}<h1>Learner details</h1><Alert onRetry={d.reload}>{d.error}</Alert></>
  const x = d.data
  return (
    <>
      {crumbs(x.student.name)}
      <h1>{x.student.name}</h1>
      <p className="muted">{x.student.student_number} · {x.student.email}</p>
      {x.flags.length > 0 && <Alert kind="warning" title="Why this learner is flagged">{x.flags.map((f) => f.message).join(' · ')}</Alert>}
      {STUDENT_PANELS.map(({ title, Component }) => (
        <div className="card" key={title}><h2 className="mt-0">{title}</h2><Component courseId={courseId} studentId={studentId} detail={x} reload={d.reload} /></div>
      ))}
      <h2>Mastery by outcome</h2>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
        <thead><tr><th>CLO</th><th>Mastery</th><th>Evidence (latest first)</th></tr></thead>
        <tbody>{x.mastery.map((m) => (
          <tr key={m.clo_id}>
            <td><strong>{m.clo_code}</strong></td>
            <td className="min-w-md">{m.current ? <><Bar value={m.current.mastery_pct} threshold={m.current.threshold} label={`${m.clo_code} mastery`} /><Badge value={m.current.attained ? 'attained' : 'below'} /></> : <Badge value="not_started" />}</td>
            <td className="small">{m.evidence.map((e) => <div key={e.id}>{e.source} {e.correct}/{e.total} ({e.score_pct}%) · {fmtDate(e.recorded_at)} {e.status !== 'active' && <Badge value={e.status} />}{e.version_no > 1 && ` v${e.version_no}`}</div>)}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {x.diagnostic && <><h2>Diagnostic readiness</h2><p>{x.diagnostic.map((r) => <Badge key={r.clo_code} value={`${r.clo_code}: ${r.level.replace('_', ' ')} ${r.score_pct}%`} tone={r.level === 'ready' ? 'ok' : r.level === 'developing' ? 'warn' : 'bad'} />)}</p></>}
      {x.path && (
        <>
          <h2>Learning path <Badge value={`v${x.path.version_no}`} />{x.path.locked && <> <Badge value="locked" /></>}</h2>
          <p className="small">{x.path.summary}</p>
          <PathDecisions url={`/paths/course/${courseId}/student/${studentId}/decisions`} />
          <PathSteps path={x.path} courseId={courseId} staff />
        </>
      )}
      <h2>Recent activity</h2>
      <ul className="small">{x.recent_activity.map((e, i) => <li key={i}>{fmtDate(e.at)} — <code>{e.action}</code></li>)}</ul>
    </>
  )
}
