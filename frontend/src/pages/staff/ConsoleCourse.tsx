/** Instructor console for one course (mockup-instructor-console.png). Uses only GET /console/course/{id}.
 *  "Where students are on the path" groups students by the step number they are on - each student's path is
 *  personal, so step numbers (not shared step names) are what the data supports. */
import { Lock, Users } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { AttainmentByOutcome, PathDistribution, type AttainmentRow, type StepCount } from '../../components/kit/Charts'
import { Card, Skeleton, StatTile, StatusPill } from '../../components/kit/Kit'
import { band } from '../../components/kit/status'
import { Alert, EmptyState, useLoad } from '../../components/ui'

type Att = { clo_id: number; clo_code: string; description: string; threshold: number; assessed: number; attained: number; attained_pct: number | null; avg_mastery: number | null }
type Flag = { type: string; clo_code: string | null; message: string }
type Need = { student_id: number; name: string; student_number: string | null; flags: Flag[]; priority: string }
type Detail = {
  course: { id: number; code: string; title: string; status: string }; enrolled: number; diagnostic_completed: number; attainment: Att[]
  pending_reviews: Record<string, number>; pending_total: number; needs_intervention: Need[]; clo_codes: string[]
  matrix: { student_id: number; name: string; student_number: string | null; clos: Record<string, { mastery_pct: number; attained: boolean } | null>; path: { version: number; locked: boolean; done: number; total: number } | null }[]
}
const KIND = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
const initials = (n: string) => n.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

export default function ConsoleCourse() {
  const { courseId = '' } = useParams()
  return <ConsoleCourseView courseId={courseId} />
}

const readCourse = () => { try { return Number(localStorage.getItem('consoleCourse')) || null } catch { return null } }
const saveCourse = (id: number) => { try { localStorage.setItem('consoleCourse', String(id)) } catch { /* storage unavailable */ } }

/** /console: the console of one course straight away, with a course switcher in the header. */
export function ConsoleHome() {
  const ov = useLoad(() => api<{ course: { id: number; code: string; title: string; program?: string | null } }[]>('/console/overview'))
  const [picked, setPicked] = useState<number | null>(readCourse)
  if (ov.loading && !ov.data) return <><h1>Console</h1><Skeleton lines={6} label="Loading the console" /></>
  if (ov.error) return <><h1>Console</h1><Alert onRetry={ov.reload}>{ov.error}</Alert></>
  const courses = (ov.data ?? []).map((x) => x.course)
  if (!courses.length) {
    return <><h1>Console</h1><EmptyState icon={Users} title="No courses to show yet">Courses you teach appear here once an administrator assigns you to a section.</EmptyState></>
  }
  const c = courses.find((x) => x.id === picked) ?? courses[0]
  const switcher = courses.length > 1 ? (
    <label className="kselect"><span className="sr-only">Course</span>
      <select value={c.id} onChange={(e) => { const id = Number(e.target.value); setPicked(id); saveCourse(id) }}>
        {courses.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.title}{x.program ? ` (${x.program})` : ''}</option>)}
      </select>
    </label>
  ) : null
  return <ConsoleCourseView key={c.id} courseId={String(c.id)} switcher={switcher} />
}

export function ConsoleCourseView({ courseId, switcher }: { courseId: string; switcher?: ReactNode }) {
  const d = useLoad(() => api<Detail>(`/console/course/${courseId}`), [courseId])
  if (d.loading && !d.data) return <><h1>Course console</h1><Skeleton lines={6} label="Loading the course console" /></>
  if (!d.data) return <><h1>Course console</h1><Alert onRetry={d.reload}>{d.error}</Alert></>
  const x = d.data
  const threshold = x.attainment[0]?.threshold ?? 60
  // class average = mean of every student's mastery on every outcome they have a score for (each score counts once)
  const scores = x.matrix.flatMap((r) => Object.values(r.clos).filter((v): v is { mastery_pct: number; attained: boolean } => v != null).map((v) => v.mastery_pct))
  const classAvg = scores.length ? Math.round(scores.reduce((t, v) => t + v, 0) / scores.length) : null
  const targetOf = (code: string) => x.attainment.find((a) => a.clo_code === code)?.threshold ?? threshold
  const escalated = new Set(x.needs_intervention.filter((n) => n.flags.some((f) => f.type === 'escalation')).map((n) => n.student_id))
  const reteach = new Set(x.needs_intervention.filter((n) => n.flags.some((f) => f.type === 'reteach_exhausted' || f.type === 'below_after_attempts')).map((n) => n.student_id))
  const pendingParts = Object.entries(x.pending_reviews).filter(([, n]) => n > 0)

  // where students are: current step number = steps done + 1
  const steps = new Map<string, StepCount>()
  for (const r of x.matrix) {
    if (!r.path) continue
    const step = r.path.done >= r.path.total ? 'Done' : `Step ${r.path.done + 1}`
    const c = steps.get(step) ?? { step, unit: r.path.done >= r.path.total ? 'Finished' : 'On the path', onTrack: 0, reteach: 0, escalated: 0 }
    if (escalated.has(r.student_id)) c.escalated++
    else if (reteach.has(r.student_id)) c.reteach++
    else c.onTrack++
    steps.set(step, c)
  }
  const dist = [...steps.values()].sort((a, b) => (a.step === 'Done' ? 1 : b.step === 'Done' ? -1 : Number(a.step.slice(5)) - Number(b.step.slice(5))))

  // attainment by outcome from the matrix (share of students per status); average from the server
  const att: AttainmentRow[] = x.attainment.map((a) => {
    const n = x.matrix.length || 1
    const counts = { attained: 0, near: 0, notAttained: 0, insufficient: 0 }
    for (const r of x.matrix) {
      const v = r.clos[a.clo_code]
      const b = band(v?.mastery_pct ?? null, a.threshold, v ? v.attained : null)
      if (b === 'attained') counts.attained++; else if (b === 'near') counts.near++; else if (b === 'not_attained') counts.notAttained++; else counts.insufficient++
    }
    const pct = (k: number) => Math.round((100 * k) / n)
    return { clo: a.clo_code, name: a.description.split(/[,.;]/)[0].slice(0, 28), attained: pct(counts.attained), near: pct(counts.near), notAttained: pct(counts.notAttained), insufficient: pct(counts.insufficient), average: a.avg_mastery == null ? null : Math.round(a.avg_mastery), threshold: a.threshold, assessed: a.assessed }
  })

  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb"><Link to="/console">Instructor / Console</Link></p>
          <h1>{x.course.code}</h1>
          <p className="phead-desc">{x.course.title} · {x.enrolled} student{x.enrolled === 1 ? '' : 's'} · target {threshold}%</p>
        </div>
        <div className="phead-actions">
          {switcher}
          <Link className="kbtn kbtn-secondary kbtn-md" to="/reviews">Open review queue</Link>
          <a className="kbtn kbtn-primary kbtn-md" href="#matrix">Open student matrix</a>
        </div>
      </div>

      <div className="ktiles">
        <StatTile label="Active students" value={x.enrolled} context={x.enrolled - x.diagnostic_completed > 0 ? `${x.enrolled - x.diagnostic_completed} haven't taken the diagnostic` : 'Everyone has taken the diagnostic'} />
        <StatTile label="Class average mastery" value={classAvg == null ? '—' : `${classAvg}%`} bar={classAvg == null ? undefined : { value: classAvg, threshold }} context={classAvg == null ? 'No assessments yet' : `across ${scores.length} outcome score${scores.length === 1 ? '' : 's'}`} />
        <StatTile label="Open escalations" value={<span className={escalated.size ? 'tone-bad' : undefined}>{escalated.size}</span>} context={`${x.needs_intervention.length} learner${x.needs_intervention.length === 1 ? '' : 's'} need attention`} />
        <StatTile label="Pending reviews" value={x.pending_total} context={pendingParts.length ? pendingParts.map(([k, n]) => `${n} ${KIND(k).toLowerCase()}`).join(' · ') : 'All caught up'} />
      </div>

      <div className="grid12">
        <Card className="span-7" title="Where students are on the path" subtitle="Current step of each student (paths are personal, so steps are numbered per student)">
          {dist.length ? <PathDistribution data={dist} withoutPath={x.matrix.filter((r) => !r.path).length} /> : <EmptyState icon={Users} title="No learning paths yet">Students get a path after their diagnostic test.</EmptyState>}
        </Card>
        <Card className="span-5" title="Needs your attention" subtitle="Learners flagged by the system" action={<Link to="/reviews">View all</Link>}>
          {x.needs_intervention.length === 0 && !pendingParts.length ? <p className="kempty">Nothing needs you right now.</p> : (
            <ul className="alist">
              {x.needs_intervention.map((n) => (
                <li key={n.student_id}>
                  <span className="avatar-sm" aria-hidden="true">{initials(n.name)}</span>
                  <Link to={`/console/${courseId}/student`} state={{ studentId: n.student_id }}>
                    <p className="alist-title">{n.name}</p>
                    <p className="alist-detail">{n.flags.map((f) => f.message).join(' · ')}</p>
                  </Link>
                  <StatusPill status={escalated.has(n.student_id) ? 'escalated' : n.priority === 'high' ? 'escalated' : 'review'}>{escalated.has(n.student_id) ? 'Escalated' : `${n.priority === 'high' ? 'High' : 'Low'} priority`}</StatusPill>
                </li>
              ))}
              {pendingParts.map(([k, n]) => (
                <li key={k}>
                  <span className="avatar-sm" aria-hidden="true">AI</span>
                  <Link to="/reviews"><p className="alist-title">{n} {KIND(k).toLowerCase()}{n === 1 ? '' : 's'} to review</p><p className="alist-detail">Waiting in the review queue</p></Link>
                  <StatusPill status="review" />
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="span-12" title="Attainment by outcome" subtitle="Share of students in each status · average on the right">
          <AttainmentByOutcome rows={att} />
        </Card>
      </div>

      <Card title="Students × outcomes" subtitle="Score per outcome · click a name to open the learner" titleId="matrix">
        {x.matrix.length === 0 ? <EmptyState icon={Users} title="No students enrolled">Students appear here once they enrol in a section of this course.</EmptyState> : (
          <div className="ktable-wrap" tabIndex={0} role="region" aria-label="Students by outcome (scroll sideways if it does not fit)">
            <table className="ktable heat">
              <thead><tr><th scope="col" className="sticky-col">Student</th>{x.clo_codes.map((c) => <th key={c} scope="col" className="num">{c}</th>)}<th scope="col" className="num">Path</th></tr></thead>
              <tbody>{x.matrix.map((r) => (
                <tr key={r.student_id}>
                  <th scope="row" className="sticky-col"><Link to={`/console/${courseId}/student`} state={{ studentId: r.student_id }}>{r.name}</Link></th>
                  {x.clo_codes.map((c) => {
                    const v = r.clos[c]
                    const b = band(v?.mastery_pct ?? null, targetOf(c), v ? v.attained : null)
                    return <td key={c} className={`num heat-${b}`}>{v ? `${Math.round(v.mastery_pct)}%` : '—'}<span className="sr-only"> {b === 'attained' ? 'attained' : b === 'near' ? 'needs practice' : b === 'not_attained' ? 'not attained' : 'not enough evidence'}</span></td>
                  })}
                  <td className="num">{r.path ? <>{r.path.done}/{r.path.total}{r.path.locked && <> <Lock className="icon-inline" aria-label="locked" /></>}</> : '—'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
