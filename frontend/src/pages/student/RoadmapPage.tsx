import { Map as MapIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { Alert, Badge, EmptyState, Loading, useLoad } from '../../components/ui'

type RCourse = { course_id: number | null; code: string | null; title: string; credits: number | null; status: 'completed' | 'enrolled' | 'open' | 'locked' | 'not_tracked'; prerequisites: { code: string; met: boolean }[] }
type Roadmap = { program: { code: string; name: string }; current_semester: number | null; semesters: { number: number; courses: RCourse[] }[] }

const STATUS: Record<RCourse['status'], { value: string; tone: string }> = {
  completed: { value: 'completed', tone: 'ok' },
  enrolled: { value: 'in_progress', tone: 'warn' },
  open: { value: 'Available to enrol', tone: 'info' },
  locked: { value: 'Locked until earlier semesters are done', tone: '' },
  not_tracked: { value: 'Not in the app (no CLOs) — for information', tone: '' },
}

/** The programme's semester roadmap: which courses the student takes each semester, and where they are now. */
export default function RoadmapPage() {
  const r = useLoad(() => api<Roadmap[]>('/enrollment/roadmap'))
  return (
    <>
      <h1>My roadmap</h1>
      <p className="muted">The courses of your programme, semester by semester. You can enrol in courses of your current semester and of any earlier semester you have not finished. Courses marked “Not in the app” are part of your study plan but are taught outside this app.</p>
      {r.loading ? <Loading variant="table" /> : r.error ? <Alert onRetry={r.reload}>{r.error}</Alert> : !r.data?.length ? (
        <EmptyState icon={MapIcon} title="No roadmap yet" action={<Link className="btn secondary" to="/courses">Browse courses</Link>}>
          Your department has no semester plan yet, or your department is not set. If you think this is wrong, ask an administrator to check your department (CS or SE) on the Users page.
        </EmptyState>
      ) : r.data.map((p) => (
        <section key={p.program.code} aria-labelledby={`prog-${p.program.code}`} className="section">
          <div className="spread">
            <h2 id={`prog-${p.program.code}`} className="m-0">{p.program.code} — {p.program.name}</h2>
            {p.current_semester ? <Badge value={`You are in Semester ${p.current_semester}`} tone="info" /> : <Badge value="Roadmap complete" tone="ok" />}
          </div>
          {p.semesters.map((s) => {
            const current = s.number === p.current_semester
            return (
              <div key={s.number} className={`card${current ? ' next-step' : ''}`} aria-current={current ? 'step' : undefined}>
                <h3 className="mt-0">Semester {s.number}{current && <span className="small muted"> · current</span>}</h3>
                <ul className="clo-list">
                  {s.courses.map((c, i) => (
                    <li key={c.course_id ?? `x${i}`}>
                      <div>{c.code ? <><strong>{c.code}</strong> <span className="muted">{c.title}</span></> : <span>{c.title}</span>}
                        {c.prerequisites.length > 0 && <div className="small muted">Prerequisites: {c.prerequisites.map((x) => `${x.code} (${x.met ? 'met' : 'not met'})`).join(', ')}</div>}
                      </div>
                      <div className="small muted">{c.credits ? `${c.credits} credits` : ''}</div>
                      <div><Badge value={STATUS[c.status].value} tone={STATUS[c.status].tone} /></div>
                    </li>
                  ))}
                </ul>
                {current && <div className="card-actions"><Link className="btn" to="/courses">Enrol in this semester's courses</Link></div>}
              </div>
            )
          })}
        </section>
      ))}
    </>
  )
}
