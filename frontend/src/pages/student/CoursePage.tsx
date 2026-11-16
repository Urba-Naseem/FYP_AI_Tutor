/** Student course page (mockup-student-course.png): stat tiles, learning path map / list, mastery over time,
 *  your outcomes. Uses only existing endpoints; loaded on demand (charts). */
import { ArrowRight, BookOpen, BookPlus, ClipboardCheck, Compass, FileText, Library, ListChecks } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { MasteryOverTime } from '../../components/kit/Charts'
import { Card, OutcomeRow, Segmented, Skeleton, StatTile, StatusPill } from '../../components/kit/Kit'
import { PathMap } from '../../components/kit/PathMap'
import { Alert, EmptyState, useLoad } from '../../components/ui'
import { masterySeries, pathNodes, type DiagResult, type MasteryClo } from './courseData'
import { CourseComplete } from './CourseComplete'
import { InstructorCard } from './InstructorCard'
import { PathSteps, STEP_LINKS, type Path } from './PathPage'
import type { Progress } from './ProgressPage'

type View = 'map' | 'list'
const readView = (): View => { try { return localStorage.getItem('pathView') === 'list' ? 'list' : 'map' } catch { return 'map' } }
const saveView = (v: View) => { try { localStorage.setItem('pathView', v) } catch { /* storage unavailable */ } }

type Enrolment = { status: string; course: { id: number; code: string; title: string }; section: { id: number; code: string }; term: string }

export default function CoursePage() {
  const { courseId = '' } = useParams()
  return <CourseView courseId={courseId} />
}

const readCourse = () => { try { return Number(localStorage.getItem('homeCourse')) || null } catch { return null } }
const saveCourse = (id: number) => { try { localStorage.setItem('homeCourse', String(id)) } catch { /* storage unavailable */ } }

/** Student Home: the course page of the student's course (a switcher when there are several). */
export function StudentCourseHome() {
  const mine = useLoad(() => api<Enrolment[]>('/enrollment/mine'))
  const [picked, setPicked] = useState<number | null>(readCourse)
  if (mine.loading && !mine.data) return <Skeleton lines={6} label="Loading your course" />
  if (mine.error) return <Alert onRetry={mine.reload}>{mine.error}</Alert>
  // current courses first, then finished ones (a completed course still has a page with its results)
  const active = [...(mine.data ?? []).filter((e) => e.status === 'enrolled'), ...(mine.data ?? []).filter((e) => e.status === 'completed')]
  if (!active.length) {
    return (
      <>
        <h1>My courses</h1>
        <EmptyState icon={BookPlus} title="You're not enrolled in a course yet" action={<Link className="btn" to="/courses">Browse courses</Link>}>
          Enrol in a course to get your diagnostic test and personal learning path.
        </EmptyState>
      </>
    )
  }
  const e = active.find((x) => x.course.id === picked) ?? active[0]
  const switcher = active.length > 1 ? (
    <label className="kselect"><span className="sr-only">Course</span>
      <select value={e.course.id} onChange={(ev) => { const id = Number(ev.target.value); setPicked(id); saveCourse(id) }}>
        {active.map((x) => <option key={x.course.id} value={x.course.id}>{x.course.code} · {x.course.title}{x.status === 'completed' ? ' (completed)' : ''}</option>)}
      </select>
    </label>
  ) : null
  return <CourseView key={e.course.id} courseId={String(e.course.id)} switcher={switcher} />
}

export function CourseView({ courseId, switcher }: { courseId: string; switcher?: ReactNode }) {
  const nav = useNavigate()
  const enrol = useLoad(() => api<Enrolment[]>('/enrollment/mine'), [])
  const mineHere = enrol.data?.find((x) => String(x.course.id) === courseId && (x.status === 'enrolled' || x.status === 'completed'))
  const course = useLoad(() => api<{ code: string; title: string; description: string | null }>(`/outcomes/courses/${courseId}`), [courseId])
  const path = useLoad(() => api<Path | null>(`/paths/me/${courseId}`), [courseId])
  const prog = useLoad(() => api<Progress>(`/progress/me/${courseId}`), [courseId])
  const mastery = useLoad(() => api<{ clos: MasteryClo[] }>(`/mastery/me/${courseId}`), [courseId])
  // diagnostic scores for the first point of each line: only read once the diagnostic is done (no side effects)
  const diagDone = !!prog.data?.clos.some((c) => c.readiness)
  const diag = useLoad(() => (diagDone ? api<{ results: DiagResult[] | null }>(`/diagnostic/${courseId}/status`) : Promise.resolve({ results: null })), [courseId, diagDone])
  const [view, setView] = useState<View>(readView)

  const threshold = prog.data?.clos[0]?.threshold ?? 60
  const p = path.data
  const next = p?.steps.find((s) => s.id === p.next_step_id)
  const nextLink = next && STEP_LINKS[next.step_type]?.(courseId, next)
  const mMap = new Map((prog.data?.clos ?? []).filter((c) => c.mastery_pct != null).map((c) => [c.clo_id, { pct: c.mastery_pct as number, attained: c.attained }]))
  const scored = (prog.data?.clos ?? []).filter((c) => c.mastery_pct != null)
  const avg = scored.length ? Math.round(scored.reduce((t, c) => t + (c.mastery_pct ?? 0), 0) / scored.length) : null
  const series = mastery.data ? masterySeries(mastery.data.clos, diag.data?.results ?? [], threshold) : []

  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb"><Link to="/learn">My courses</Link> / {course.data?.code ?? 'Course'}</p>
          <h1>{course.data?.title ?? 'Course'}</h1>
          <p className="phead-desc">{[mineHere?.term, mineHere ? `Section ${mineHere.section.code}` : null].filter(Boolean).join(' · ') || course.data?.code}</p>
        </div>
        <div className="phead-actions">
          {switcher}
          {p && <Segmented label="Learning path view" options={[{ value: 'map', label: 'Map' }, { value: 'list', label: 'List' }]} value={view} onChange={(v) => { setView(v); saveView(v) }} />}
          {nextLink ? <Link className="kbtn kbtn-primary kbtn-md" to={nextLink}>Continue: {next.label} ({next.clo_code})<ArrowRight aria-hidden="true" /></Link>
            : !p && <Link className="kbtn kbtn-primary kbtn-md" to={`/learn/${courseId}/diagnostic`}>Take the diagnostic<ArrowRight aria-hidden="true" /></Link>}
        </div>
      </div>
      {course.error && <Alert onRetry={course.reload}>{course.error}</Alert>}
      {prog.data && ((p && !next && p.steps.length > 0) || mineHere?.status === 'completed') && (
        <CourseComplete courseId={courseId} title={course.data?.title ?? 'this course'} clos={prog.data.clos} officiallyCompleted={mineHere?.status === 'completed'} />
      )}

      {prog.loading && !prog.data ? <Skeleton lines={2} label="Loading your progress" /> : prog.data && (
        <div className="ktiles">
          <StatTile label="Outcomes attained" value={prog.data.summary.attained} suffix={`/ ${prog.data.summary.total}`} context={`${Math.round(prog.data.summary.attained_pct)}% of the course outcomes`} />
          <StatTile label="Average mastery" value={avg == null ? '—' : `${avg}%`} bar={avg == null ? undefined : { value: avg, threshold }} context={avg == null ? 'No attempts yet' : `across ${scored.length} of ${mastery.data?.clos.length ?? scored.length} outcomes with a score`} />
          <StatTile label="Path progress" value={prog.data.summary.path_done} suffix={`/ ${prog.data.summary.path_total} steps`} context={next ? `Next: ${next.label} (${next.clo_code})` : p ? 'Path complete' : 'No path yet'} />
          <StatTile label="Attainment target" value={`${threshold}%`} context="Set by your course" />
        </div>
      )}

      <Card title="Your learning path"
        subtitle={p ? `Version ${p.version_no}${p.reason ? ` · ${p.reason}` : ''} · ${view === 'map' ? 'click a step to see why it is there' : 'each step shows why it is there'}` : undefined}
        action={p ? <StatusPill status={p.locked ? 'locked' : 'on_track'}>{p.locked ? 'Locked by your instructor' : 'Path on track'}</StatusPill> : undefined}>
        {path.loading && !p ? <Skeleton lines={4} label="Loading your learning path" /> : path.error ? <Alert onRetry={path.reload}>{path.error}</Alert> : !p ? (
          <EmptyState icon={Compass} title="No learning path yet" action={<Link className="btn" to={`/learn/${courseId}/diagnostic`}>Take the diagnostic</Link>}>
            Your personal path is built from your diagnostic test.
          </EmptyState>
        ) : view === 'map' ? (
          <PathMap nodes={pathNodes(p, courseId, threshold, mMap, diagDone)} locked={p.locked} onGo={(n) => n.href && nav(n.href)} />
        ) : <PathSteps path={p} courseId={courseId} quietNext={!!nextLink} />}
      </Card>

      <div className="grid12">
        <Card className="span-7" title="Mastery over time" subtitle={`Mastery after each quiz (best of your last 3) · red line = ${threshold}% target`}>
          {mastery.loading && !mastery.data ? <Skeleton lines={5} label="Loading mastery history" /> : mastery.error ? <Alert onRetry={mastery.reload}>{mastery.error}</Alert>
            : series.some((s) => s.points.length) ? <MasteryOverTime series={series} /> : <p className="kempty">No attempts yet — take the diagnostic to see your progress.</p>}
        </Card>
        <Card className="span-5" title="Your outcomes" subtitle={`Target: ${threshold}% · based on your latest attempts`}>
          {prog.data?.clos.length ? (
            <div className="orows">
              {prog.data.clos.map((c) => (
                <OutcomeRow key={c.clo_id} code={c.clo_code} description={c.description} value={c.mastery_pct} threshold={c.threshold} attained={c.mastery_pct == null ? null : c.attained}
                  note={c.attempts ? `${c.attempts} attempt${c.attempts === 1 ? '' : 's'}` : c.readiness ? 'From diagnostic' : 'No attempts yet'} />
              ))}
            </div>
          ) : prog.loading ? <Skeleton lines={5} /> : <p className="kempty">No outcomes published yet.</p>}
        </Card>
      </div>

      <div className="grid12">
        <InstructorCard className="span-12" courseId={courseId} />
      </div>

      <nav className="course-tiles" aria-label="More in this course">
        {[
          { to: `/learn/${courseId}/units`, Icon: BookOpen, title: 'Course material', help: 'All topics and lessons, open any time' },
          { to: `/learn/${courseId}/reading`, Icon: Library, title: 'Suggested reading', help: 'Books, notes and slides from your instructor' },
          { to: `/learn/${courseId}/practice`, Icon: ListChecks, title: 'Formative quizzes', help: 'Marked quizzes, unlocked step by step' },
          { to: `/learn/${courseId}/diagnostic`, Icon: ClipboardCheck, title: 'Diagnostic test', help: 'Your starting point for this course' },
          { to: `/learn/${courseId}/progress`, Icon: FileText, title: 'Progress report', help: 'Results, past quizzes and downloads' },
        ].map(({ to, Icon, title, help }) => (
          <Link key={to} to={to} className="course-tile">
            <span className="course-tile-icon" aria-hidden="true"><Icon /></span>
            <span><strong>{title}</strong><span className="course-tile-help">{help}</span></span>
          </Link>
        ))}
      </nav>
    </>
  )
}
