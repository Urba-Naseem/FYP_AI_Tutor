/** Past quizzes: the student's finished formative / reteach quizzes, and a review of one quiz - every question with
 *  their answer, whether it was right, the correct answer and the explanation. (The diagnostic is not reviewable:
 *  everyone takes it, so its answers stay hidden.) Uses GET /quiz/history/{course} and GET /quiz/attempts/{id}. */
import { Check, X } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { Card, Skeleton, StatusPill } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, fmtDate, useLoad } from '../../components/ui'

type Past = { id: number; kind: string; clo_code: string; score_pct: number | null; correct: number; total: number; completed_at: string }
type Q = { id: number; stem: string; options: string[]; answer?: { chosen: number; correct: boolean; correct_index: number; explanation: string; hints_used: number } }
type Attempt = { id: number; course_id: number; clo_id: number; clo_code: string; clo_description: string; kind: string; status: string; questions: Q[]; score_pct: number | null; total: number }

/** Pass mark per outcome (the course's own target; 60% when none is set). Read-only, students may read it. */
function useTargets(courseId: string) {
  const t = useLoad(() => api<{ clos: { clo_code: string; threshold: number }[] }>(`/criteria/effective/course/${courseId}`), [courseId])
  return (clo: string) => t.data?.clos.find((c) => c.clo_code === clo)?.threshold ?? 60
}

/** List for the progress page. */
export function PastQuizzes({ courseId }: { courseId: string }) {
  const h = useLoad(() => api<Past[]>(`/quiz/history/${courseId}`), [courseId])
  const target = useTargets(courseId)
  return (
    <Card title="Past quizzes" subtitle="Open a finished quiz to see your answers, the correct ones and why">
      {h.loading && !h.data ? <Skeleton lines={3} label="Loading your past quizzes" /> : h.error ? <Alert onRetry={h.reload}>{h.error}</Alert> : !h.data?.length ? (
        <p className="kempty">No finished quizzes yet — your practice and reteach quizzes appear here.</p>
      ) : (
        <ul className="pastq">
          {h.data.map((p) => (
            <li key={p.id}>
              <div className="min-w-0">
                <Link to={`/learn/${courseId}/quiz-review/${p.id}`} className="pastq-title">{p.kind === 'reteach' ? 'Reteach quiz' : 'Practice quiz'} · {p.clo_code}</Link>
                <p className="pastq-meta">{fmtDate(p.completed_at)}</p>
              </div>
              <span className="pastq-score tabular">{p.correct} / {p.total} correct</span>
              <StatusPill status={p.score_pct != null && p.score_pct >= target(p.clo_code) ? 'attained' : 'near'}>{p.score_pct == null ? '—' : `${Math.round(p.score_pct)}%`}</StatusPill>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function QuizReviewPage() {
  const { courseId = '', aid = '' } = useParams()
  const a = useLoad(() => api<Attempt>(`/quiz/attempts/${aid}`), [aid])
  const x = a.data
  const right = x?.questions.filter((q) => q.answer?.correct).length ?? 0
  const target = useTargets(courseId)
  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb"><Link to={`/learn/${courseId}/progress`}>My progress</Link> / Past quiz</p>
          <h1>{x ? `${x.kind === 'reteach' ? 'Reteach quiz' : 'Formative quiz'} · ${x.clo_code}` : 'Past quiz'}</h1>
          {x && <p className="phead-desc">{x.clo_description}</p>}
        </div>
        {x && <div className="phead-actions"><StatusPill status={(100 * right) / Math.max(1, x.total) >= target(x.clo_code) ? 'attained' : 'near'}>{right} of {x.total} correct</StatusPill></div>}
      </div>
      {a.loading && !x ? <Skeleton lines={6} label="Loading the quiz" /> : a.error ? <Alert onRetry={a.reload}>{a.error}</Alert> : x && (
        x.status !== 'completed' ? <Alert kind="info">This quiz is not finished yet. <Link to={`/learn/${courseId}/quiz/${x.clo_id}`}>Go back to it</Link> to see the answers when you are done.</Alert> : (
          <ol className="qreview">
            {x.questions.map((q, n) => {
              const ans = q.answer
              return (
                <li key={q.id} className={`kcard qreview-item ${ans?.correct ? 'is-right' : 'is-wrong'}`}>
                  <div className="kcard-body">
                    <p className="qreview-head">
                      <span>Question {n + 1}</span>
                      {ans ? (ans.correct ? <StatusPill status="attained">Correct</StatusPill> : <StatusPill status="not_attained">Wrong</StatusPill>) : <StatusPill status="insufficient">Not answered</StatusPill>}
                    </p>
                    <Markdown>{q.stem}</Markdown>
                    <ul className="qreview-options">
                      {q.options.map((o, i) => {
                        const isCorrect = ans?.correct_index === i
                        const isChosen = ans?.chosen === i
                        return (
                          <li key={i} className={`${isCorrect ? 'is-correct' : ''}${isChosen && !isCorrect ? ' is-chosen-wrong' : ''}`}>
                            <span className="qreview-letter" aria-hidden="true">{String.fromCharCode(65 + i)}</span>
                            <span className="qreview-text">{o}</span>
                            {isCorrect && <span className="qreview-tag"><Check aria-hidden="true" />Correct answer</span>}
                            {isChosen && !isCorrect && <span className="qreview-tag"><X aria-hidden="true" />Your answer</span>}
                            {isChosen && isCorrect && <span className="sr-only"> (your answer)</span>}
                          </li>
                        )
                      })}
                    </ul>
                    {ans?.explanation && <div className="qreview-why"><strong>Why:</strong> <Markdown>{ans.explanation}</Markdown></div>}
                    {ans && ans.hints_used > 0 && <p className="caption">Hints used: {ans.hints_used}</p>}
                  </div>
                </li>
              )
            })}
          </ol>
        )
      )}
      <p><Link to={`/learn/${courseId}/progress`}>Back to my progress</Link></p>
    </>
  )
}
