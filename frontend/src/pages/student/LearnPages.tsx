import { BookPlus } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { Alert, Crumbs, EmptyState, Loading, useLoad } from '../../components/ui'

type C = { id: number; code: string; title: string }

/** Student hub: the courses I am enrolled in. */
export function MyLearningPage() {
  const { data, loading, error, reload } = useLoad(() => api<C[]>('/diagnostic/courses/mine'))
  return (
    <>
      <h1>My learning</h1>
      <p className="muted">The courses you are enrolled in.</p>
      {loading ? <Loading variant="cards" /> : error ? <Alert onRetry={reload}>{error}</Alert> : !data?.length ? (
        <EmptyState icon={BookPlus} title="You're not enrolled in any course yet" action={<Link className="btn" to="/courses">Browse courses</Link>}>
          Enrol in a course to start learning.
        </EmptyState>
      ) : (
        <div className="grid">
          {data.map((c) => (
            <Link key={c.id} to={`/learn/${c.id}`} className="card">
              <strong>{c.code}</strong><span className="muted">{c.title}</span>
            </Link>
          ))}
        </div>
      )}
    </>
  )
}

// Sections of the course home; later features register more entries.
export const COURSE_SECTIONS: { path: string; label: string; help: string }[] = [
  { path: 'diagnostic', label: 'Diagnostic test', help: 'Find out what you already know for each outcome.' },
  { path: 'units', label: 'Course material', help: 'Read the approved lessons for each unit.' },
  { path: 'practice', label: 'Formative quizzes', help: 'Take the marked quiz for any outcome and see your mastery.' },
  { path: 'path', label: 'My learning path', help: 'Your personalised sequence of study and practice, with reasons.' },
  { path: 'progress', label: 'My progress', help: 'Mastery per outcome, weak areas, next steps and a downloadable report.' },
]

export function CourseHomePage() {
  const { courseId } = useParams()
  const { data, loading, error, reload } = useLoad(() => api<{ code: string; title: string; description: string | null }>(`/outcomes/courses/${courseId}`), [courseId])
  return (
    <>
      <Crumbs items={[{ to: '/learn', label: 'My learning' }, { label: data?.code ?? 'Course' }]} />
      <h1>{data ? `${data.code} — ${data.title}` : 'Course home'}</h1>
      {loading && !data && <Loading variant="lines" />}
      {error && <Alert onRetry={reload}>{`We couldn't load the course details: ${error}`}</Alert>}
      {data?.description && <p className="muted">{data.description}</p>}
      <div className="grid">
        {COURSE_SECTIONS.map((s) => (
          <Link key={s.path} to={`/learn/${courseId}/${s.path}`} className="card">
            <strong>{s.label}</strong><span className="small muted">{s.help}</span>
          </Link>
        ))}
      </div>
    </>
  )
}
