import { useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import Markdown from '../../components/Markdown'
import { Alert, Badge, Bar, Crumbs, Loading, useLoad } from '../../components/ui'
import { STEP_LINKS } from './PathPage'
import { QuizRunner, type Attempt } from './QuizPage'

type Cycle = { id: number; clo_code: string; clo_description: string; status: string; attempts_used: number; max_attempts: number; attempts_left: number; explanation_source: string; explanation_md: string; mastery: { mastery_pct: number; threshold: number; attained: boolean } | null; open_quiz_id: number | null; fresh_questions: number }

STEP_LINKS.reteach = (c, s) => `/learn/${c}/reteach/${s.clo_id}?step=${s.id}`

const SOURCE_LABEL: Record<string, string> = {
  approved_alternate: 'Explanation approved by your instructor',
  ai_grounded: 'AI explanation based on the outcome and suggested reading',
  ai_curriculum: 'AI explanation based on this learning outcome',
  offline_note: 'Study note for this outcome',
  offline_extract: 'Extracts from suggested reading',
}

export default function ReteachPage() {
  const { courseId = '', cloId } = useParams()
  const [params] = useSearchParams()
  const step = params.get('step')
  const cyc = useLoad(() => api<Cycle>(`/reteach/${courseId}/clo/${cloId}`), [courseId, cloId])
  const [quiz, setQuiz] = useState(false)
  const crumbs = <Crumbs items={[{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}/path`, label: 'My learning path' }, { label: 'Reteach' }]} />
  if (cyc.loading && !cyc.data) return <>{crumbs}<Loading /></>
  if (!cyc.data) return <>{crumbs}<h1>Reteach</h1><Alert onRetry={cyc.reload}>{cyc.error}</Alert></>
  const c = cyc.data
  return (
    <div className="reading">
      {crumbs}
      <h1>Let's look at {c.clo_code} another way</h1>
      <p className="muted">{c.clo_description}</p>
      {c.mastery && (
        <div className="card">
          <Bar value={c.mastery.mastery_pct} threshold={c.mastery.threshold} label={`${c.clo_code} mastery`} />
          <p className="small m-0">Current mastery {c.mastery.mastery_pct}% · target {c.mastery.threshold}% · reteach attempts used {c.attempts_used} of {c.max_attempts}</p>
        </div>
      )}
      {c.status === 'exhausted' && <Alert kind="warning" title="No reteach attempts left">You have used all reteach attempts for this outcome. Carry on with your path — your instructor will follow up with you.</Alert>}
      {c.status === 'passed' && <Alert kind="success" title="Outcome mastered">You reached the target for this outcome after reteaching.</Alert>}
      <div className="card">
        <Badge value={SOURCE_LABEL[c.explanation_source] ?? c.explanation_source} />
        <Markdown>{c.explanation_md}</Markdown>
      </div>
      {c.status === 'active' && (quiz ? (
        <QuizRunner backTo={`/learn/${courseId}/path`} start={() => api<Attempt>(`/reteach/cycles/${c.id}/quiz`, { body: { step_id: step ? Number(step) : null } })} />
      ) : (
        <div className="card">
          <h2 className="mt-0">Check your understanding</h2>
          <p>When you're ready, try a short quiz with <strong>new questions</strong> you haven't seen before ({c.attempts_left} attempt{c.attempts_left === 1 ? '' : 's'} left).</p>
          {c.fresh_questions < 3 && !c.open_quiz_id ? <Alert kind="info" title="No new questions yet">There are not enough new questions for this outcome. Please ask your instructor.</Alert> : (
            <div className="card-actions"><button onClick={() => setQuiz(true)}>{c.open_quiz_id ? 'Continue reteach quiz' : 'Start reteach quiz'}</button></div>
          )}
        </div>
      ))}
    </div>
  )
}
