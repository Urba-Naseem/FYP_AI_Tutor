import { AlertCircle, Check, CheckCircle2, ListChecks, Lock, X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import Markdown from '../../components/Markdown'
import { Alert, Badge, Bar, Crumbs, EmptyState, Loading, useAction, useLoad } from '../../components/ui'
import { STEP_LINKS } from './PathPage'
import { PracticePanel } from './PracticePanel'

type Ans = { chosen: number; correct: boolean; correct_index: number; explanation: string; hints_used?: number }
export type QQ = { id: number; stem: string; options: string[]; answer?: Ans }
export type Attempt = {
  id: number; course_id: number; clo_id: number; clo_code: string; clo_description: string; kind: string; status: string; step_id: number | null
  questions: QQ[]; answered: number; total: number; score_pct: number | null
  mastery?: { mastery_pct: number; attained: boolean; threshold: number; attempts_counted: number; rule_version: string }
  adaptation?: { decision: string; message: string; rule: string } | null
}

STEP_LINKS.practice = (c, s) => `/learn/${c}/quiz/${s.clo_id}?step=${s.id}`
STEP_LINKS.review = (c, s) => `/learn/${c}/quiz/${s.clo_id}?step=${s.id}`

// Slot for the hint panel (US-6.2) so the quiz page stays independent of it.
export const QuizExtras: { hint?: (attemptId: number, q: QQ) => ReactNode } = {}

export function QuizRunner({ start, backTo }: { start: () => Promise<Attempt>; backTo: string }) {
  const [att, setAtt] = useState<Attempt | null>(null)
  const [idx, setIdx] = useState(0)
  const [choice, setChoice] = useState<number | null>(null)
  const act = useAction()

  useEffect(() => {
    act.run(start).then((a) => {
      if (a) {
        setAtt(a)
        const first = a.questions.findIndex((q) => !q.answer)
        setIdx(first === -1 ? a.questions.length - 1 : first)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!att) return act.error ? <Alert>{act.error}</Alert> : <Loading variant="cards" label="Preparing your quiz…" />
  const q = att.questions[idx]
  const allAnswered = att.questions.every((x) => x.answer)

  async function submit() {
    if (choice === null) return
    const r = await act.run(() => api<Ans>(`/quiz/attempts/${att!.id}/answer`, { body: { item_id: q.id, choice } }))
    if (r) {
      const qs = att!.questions.map((x) => (x.id === q.id ? { ...x, answer: { ...r, chosen: choice } } : x))
      setAtt({ ...att!, questions: qs, answered: att!.answered + 1 })
    }
  }
  async function finish() {
    const r = await act.run(() => api<Attempt>(`/quiz/attempts/${att!.id}/finish`, { method: 'POST' }))
    if (r) setAtt(r)
  }
  const go = (i: number) => { setIdx(i); setChoice(null) }

  if (att.status === 'completed' && att.mastery) {
    return (
      <section className="card question" aria-labelledby="quiz-result">
        <h2 id="quiz-result" className="mt-0">Result for {att.clo_code}: {att.score_pct}%</h2>
        <Bar value={att.mastery.mastery_pct} threshold={att.mastery.threshold} label={`${att.clo_code} mastery`} />
        <p>Your mastery of {att.clo_code} is now <strong>{att.mastery.mastery_pct}%</strong> (best of your last {att.mastery.attempts_counted} attempt{att.mastery.attempts_counted > 1 ? 's' : ''}). The mastery target is {att.mastery.threshold}%.</p>
        <p><Badge value={att.mastery.attained ? 'attained' : 'below'} /></p>
        {att.mastery.attained ? <Alert kind="success" title="Outcome mastered">Well done — you reached the target for {att.clo_code}.</Alert> : <Alert kind="info" title="Not mastered yet">Keep going: your learning path will suggest what to study next.</Alert>}
        {att.adaptation && <Alert kind="info" title="What happens next">{att.adaptation.message} <span className="small muted">({att.adaptation.rule})</span></Alert>}
        <div className="card-actions"><Link className="btn" to={backTo}>Back to my learning path</Link></div>
      </section>
    )
  }

  const isLast = idx === att.total - 1
  return (
    <section className="card question" aria-label={`Question ${idx + 1} of ${att.total}`}>
      <div className="question-top">
        <span>{att.kind === 'reteach' ? 'Reteach quiz' : 'Formative quiz'} · {att.clo_code} · Question {idx + 1} of {att.total}</span>
        <span className="save-status"><CheckCircle2 className="icon-inline" aria-hidden="true" />{att.answered} of {att.total} answered and saved</span>
      </div>
      <Bar value={(100 * att.answered) / att.total} label="Quiz progress" />
      <div className="question-stem"><Markdown>{q.stem}</Markdown></div>
      {!q.answer && QuizExtras.hint?.(att.id, q)}
      <fieldset>
        <legend className="sr-only">Choose one answer</legend>
        {q.options.map((o, i) => {
          const cls = q.answer ? (i === q.answer.correct_index ? 'correct' : i === q.answer.chosen ? 'wrong' : '') : ''
          return (
            <label key={i} className={`option ${cls}`}>
              <input type="radio" name={`q${q.id}`} disabled={!!q.answer} checked={q.answer ? q.answer.chosen === i : choice === i} onChange={() => setChoice(i)} />
              <span>{String.fromCharCode(65 + i)}. {o}</span>
              {cls === 'correct' && <span className="option-mark"><Check className="icon-inline" aria-hidden="true" />{i === q.answer?.chosen ? 'Your answer — correct' : 'Correct answer'}</span>}
              {cls === 'wrong' && <span className="option-mark"><X className="icon-inline" aria-hidden="true" />Your answer</span>}
            </label>
          )
        })}
      </fieldset>
      {q.answer && (
        <div className={`feedback ${q.answer.correct ? 'correct' : 'incorrect'}`} role="status">
          {q.answer.correct ? <CheckCircle2 className="icon" aria-hidden="true" /> : <AlertCircle className="icon" aria-hidden="true" />}
          <div>
            <strong className="feedback-title">{q.answer.correct ? 'Correct' : `Not quite — the answer is ${String.fromCharCode(65 + q.answer.correct_index)}`}</strong>
            {q.answer.explanation}
            {!q.answer.correct && <p className="small m-0 mt-2">Read the explanation, then try the next question. Your learning path will help you review this outcome.</p>}
          </div>
        </div>
      )}
      <Alert>{act.error}</Alert>
      <div className="question-nav">
        <button className="secondary" disabled={idx === 0} onClick={() => go(idx - 1)}>Previous</button>
        <div className="row">
          {!q.answer && <button disabled={choice === null || act.busy} aria-busy={act.busy || undefined} onClick={submit}>Check answer</button>}
          {q.answer && !isLast && <button className={allAnswered ? 'secondary' : undefined} onClick={() => go(idx + 1)}>Next question</button>}
          {allAnswered && <button onClick={finish} disabled={act.busy} aria-busy={act.busy || undefined}>Submit quiz</button>}
        </div>
      </div>
    </section>
  )
}

export default function QuizPage() {
  const { courseId = '', cloId } = useParams()
  const [params] = useSearchParams()
  const step = params.get('step')
  const [started, setStarted] = useState(false)
  const info = useLoad(() => api<{ clo_id: number; clo_code: string; description: string; locked_reason: string | null }[]>(`/quiz/mastery/${courseId}`), [courseId])
  const clo = info.data?.find((r) => String(r.clo_id) === cloId)
  return (
    <>
      <Crumbs items={[{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}/path`, label: 'My learning path' }, { label: 'Formative quiz' }]} />
      <h1>Formative quiz{clo ? ` · ${clo.clo_code}` : ''}</h1>
      {started ? (
        <QuizRunner backTo={`/learn/${courseId}/path`} start={() => api<Attempt>('/quiz/start', { body: { course_id: Number(courseId), clo_id: Number(cloId), step_id: step ? Number(step) : null } })} />
      ) : (
        <>
          <div className="card">
            {clo && <p className="m-0">{clo.description}</p>}
            <p className="small muted">This quiz counts toward your mastery. Its questions are picked for your level and you never get the same question twice. Want to warm up first? Try the practice questions below — they are not marked.</p>
            {clo?.locked_reason ? (
              <Alert kind="info" title="This quiz is not open yet">{clo.locked_reason} <Link to={`/learn/${courseId}`}>Go to my path</Link></Alert>
            ) : <div className="card-actions"><button onClick={() => setStarted(true)}>Start the quiz</button></div>}
          </div>
          {clo && <PracticePanel courseId={courseId} cloId={clo.clo_id} cloCode={clo.clo_code} title="Warm up first (optional)" />}
        </>
      )}
    </>
  )
}

export function PracticeHubPage() {
  const { courseId = '' } = useParams()
  const rows = useLoad(() => api<{ clo_id: number; clo_code: string; description: string; quiz_available: boolean; mastery_pct: number | null; attained: boolean | null; threshold: number | null; locked_reason: string | null }[]>(`/quiz/mastery/${courseId}`), [courseId])
  return (
    <>
      <Crumbs items={[{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: 'Course' }, { label: 'Formative quizzes' }]} />
      <h1>Formative quizzes by outcome</h1>
      <p className="muted">Quiz yourself on any outcome. Your mastery updates after each quiz.</p>
      {rows.loading ? <Loading variant="table" /> : rows.error ? <Alert onRetry={rows.reload}>{rows.error}</Alert> : !rows.data?.length ? (
        <EmptyState icon={ListChecks} title="Nothing to practise yet">This course has no learning outcomes published yet. Check back once your instructor has published them.</EmptyState>
      ) : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
          <thead><tr><th>Outcome</th><th>Mastery</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{rows.data.map((r) => (
            <tr key={r.clo_id}>
              <td><strong>{r.clo_code}</strong> <span className="small muted">{r.description}</span></td>
              <td className="min-w-md">{r.mastery_pct === null ? <span className="muted">Not assessed yet</span> : <Bar value={r.mastery_pct} threshold={r.threshold ?? 60} label={`${r.clo_code} mastery`} />}</td>
              <td><Badge value={r.mastery_pct === null ? 'not_started' : r.attained ? 'attained' : 'below'} /></td>
              <td>{r.locked_reason ? <span className="small muted"><Lock className="icon-inline" aria-hidden="true" /> Locked — {r.locked_reason}</span> : r.quiz_available ? <Link className="btn secondary small" to={`/learn/${courseId}/quiz/${r.clo_id}`}>Take quiz<span className="sr-only"> for {r.clo_code}</span></Link> : <span className="small muted">No quiz yet</span>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  )
}
