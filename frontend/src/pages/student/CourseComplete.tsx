/** Shown on the course page when the learning path is finished (or the course is marked completed): a clear
 *  "you finished" with results, which outcomes still need work, and what to do next. */
import { Award, Download, ListChecks, Map as MapIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card, StatusPill } from '../../components/kit/Kit'

type Clo = { clo_id: number; clo_code: string; mastery_pct: number | null; attained: boolean; threshold: number }

export function CourseComplete({ courseId, title, clos, officiallyCompleted }: { courseId: string; title: string; clos: Clo[]; officiallyCompleted: boolean }) {
  const attained = clos.filter((c) => c.attained)
  const open = clos.filter((c) => !c.attained)
  const scored = clos.filter((c) => c.mastery_pct != null)
  const avg = scored.length ? Math.round(scored.reduce((t, c) => t + (c.mastery_pct ?? 0), 0) / scored.length) : null
  const all = open.length === 0 && clos.length > 0
  return (
    <Card className="course-done" title={officiallyCompleted ? `${title} — course completed` : `You finished your learning path in ${title}`}
      subtitle={officiallyCompleted ? 'Your enrolment is marked completed.' : 'Every step of your personal path is done.'}
      action={<StatusPill status={all ? 'attained' : 'near'}>{all ? 'All outcomes attained' : `${open.length} outcome${open.length === 1 ? '' : 's'} still below target`}</StatusPill>}>
      <div className="course-done-body">
        <Award className="course-done-icon" aria-hidden="true" />
        <div>
          <p className="m-0"><strong>{attained.length} of {clos.length}</strong> outcomes attained{avg != null && <> · average mastery <strong>{avg}%</strong></>}.</p>
          {open.length > 0 ? (
            <p className="small">Still below target: {open.map((c) => `${c.clo_code} (${c.mastery_pct == null ? 'no score' : `${Math.round(c.mastery_pct)}%`} of ${c.threshold}%)`).join(', ')}. You can keep practising these with practice questions and formative quizzes.</p>
          ) : <p className="small">Well done — you reached the target on every outcome of this course.</p>}
          <div className="card-actions">
            <Link className="kbtn kbtn-primary kbtn-md" to={`/learn/${courseId}/progress`}><Download aria-hidden="true" />Progress report</Link>
            <Link className="kbtn kbtn-secondary kbtn-md" to={`/learn/${courseId}/practice`}><ListChecks aria-hidden="true" />Formative quizzes</Link>
            <Link className="kbtn kbtn-secondary kbtn-md" to="/roadmap"><MapIcon aria-hidden="true" />What's next in my roadmap</Link>
          </div>
        </div>
      </div>
    </Card>
  )
}
