import { Check, CheckCircle2, ListChecks, Lock, X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { askConfirm } from '../../components/dialogApi'
import Markdown from '../../components/Markdown'
import { Alert, Badge, Bar, Crumbs, EmptyState, Loading, useAction, useLoad } from '../../components/ui'
import { STEP_LINKS } from './PathPage'
import { PracticePanel } from './PracticePanel'
import { QuizDebrief } from './QuizDebrief'

export type QType = 'mcq' | 'true_false' | 'fill_blank' | 'short_answer'
type Resp = { choice?: number; text?: string }
type Ans = {
  chosen?: number; correct: boolean; correct_index?: number; explanation: string; hints_used?: number
  text?: string; model_answer?: string; feedback?: string; key_points?: string[]
}
export type QQ = { id: number; type?: QType; stem: string; options: string[]; response?: Resp; answer?: Ans; hints_available?: boolean }
export type Attempt = {
  id: number; course_id: number; clo_id: number; clo_code: string; clo_description: string; kind: string; status: string; step_id: number | null
  questions: QQ[]; answered: number; total: number; score_pct: number | null; correct?: number
  mastery?: { mastery_pct: number; attained: boolean; threshold: number; attempts_counted: number; rule_version: string }
  adaptation?: { decision: string; message: string; rule: string } | null
}

STEP_LINKS.practice = (c, s) => `/learn/${c}/quiz/${s.clo_id}?step=${s.id}`
STEP_LINKS.review = (c, s) => `/learn/${c}/quiz/${s.clo_id}?step=${s.id}`

// Slot for the hint panel (US-6.2, reteach quizzes only) so the quiz page stays independent of it.
export const QuizExtras: { hint?: (attemptId: number, q: QQ) => ReactNode } = {}

const TYPE_LABEL: Record<QType, string> = { mcq: 'Multiple choice', true_false: 'True or false', fill_blank: 'Fill in the blank', short_answer: 'Short answer' }
const LETTER = (i: number) => String.fromCharCode(65 + i)
const has = (r?: Resp) => !!r && (r.choice !== undefined || !!r.text?.trim())
const same = (a?: Resp, b?: Resp) => (a?.choice ?? null) === (b?.choice ?? null) && (a?.text ?? '').trim() === (b?.text ?? '').trim()

/** One question, answered but not marked: radio options (MCQ, true/false) or a text box (fill in the blank, short answer). */
function QuestionInput({ q, value, onChange }: { q: QQ; value?: Resp; onChange: (r: Resp) => void }) {
  const t = q.type ?? 'mcq'
  if (t === 'mcq' || t === 'true_false') {
    const pick = (i: number) => onChange(t === 'mcq' ? { choice: i } : { text: i === 0 ? 'true' : 'false' })
    const checked = (i: number) => (t === 'mcq' ? value?.choice === i : value?.text?.toLowerCase() === (i === 0 ? 'true' : 'false'))
    return (
      <fieldset>
        <legend className="sr-only">Choose one answer</legend>
        {q.options.map((o, i) => (
          <label key={i} className="option">
            <input type="radio" name={`q${q.id}`} checked={checked(i)} onChange={() => pick(i)} />
            <span className="option-text">{t === 'mcq' && <>{LETTER(i)}. </>}<Markdown inline>{o}</Markdown></span>
          </label>
        ))}
      </fieldset>
    )
  }
  const id = `ans-${q.id}`
  return (
    <div className="field">
      <label htmlFor={id}>{t === 'fill_blank' ? 'The missing word, phrase or number' : 'Your answer (one or two sentences, in your own words)'}</label>
      {t === 'fill_blank'
        ? <input id={id} value={value?.text ?? ''} onChange={(e) => onChange({ text: e.target.value })} autoComplete="off" maxLength={200} />
        : <textarea id={id} rows={4} value={value?.text ?? ''} onChange={(e) => onChange({ text: e.target.value })} maxLength={2000} />}
    </div>
  )
}

export function QuizRunner({ start, backTo, courseId }: { start: () => Promise<Attempt>; backTo: string; courseId?: string }) {
  const [att, setAtt] = useState<Attempt | null>(null)
  const [idx, setIdx] = useState(0)
  const [drafts, setDrafts] = useState<Record<number, Resp>>({})
  const act = useAction()

  useEffect(() => {
    act.run(start).then((a) => {
      if (a) {
        setAtt(a)
        setDrafts(Object.fromEntries(a.questions.filter((q) => q.response).map((q) => [q.id, q.response!])))
        const first = a.questions.findIndex((q) => !q.response)
        setIdx(first === -1 ? 0 : first)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!att) return act.error ? <Alert>{act.error}</Alert> : <Loading variant="cards" label="Preparing your quiz — writing a few questions for you, this can take a few seconds…" />
  if (att.status === 'completed') return <QuizResult att={att} backTo={backTo} courseId={courseId ?? String(att.course_id)} />

  const q = att.questions[idx]
  const draft = drafts[q.id]

  /** Save the current question's answer if it is new or changed (answers are not marked until the quiz is submitted). */
  async function save(): Promise<boolean> {
    if (!att || !has(draft) || same(draft, q.response)) return true
    const body = (q.type ?? 'mcq') === 'mcq' ? { item_id: q.id, choice: draft!.choice } : { item_id: q.id, text: draft!.text }
    const r = await act.run(() => api<{ answered: number }>(`/quiz/attempts/${att.id}/answer`, { body }))
    if (!r) return false
    setAtt({ ...att, answered: r.answered, questions: att.questions.map((x) => (x.id === q.id ? { ...x, response: draft } : x)) })
    return true
  }
  const go = async (i: number) => { if (i !== idx && (await save())) setIdx(i) }
  const answeredNow = att.questions.filter((x) => has(x.id === q.id ? draft : x.response)).length
  async function finish() {
    if (!(await save())) return
    if (!(await askConfirm({ title: 'Submit your quiz?', message: 'You cannot change your answers after submitting. You will see your result and go through any mistakes with your tutor.', confirmLabel: 'Submit quiz' }))) return
    const r = await act.run(() => api<Attempt>(`/quiz/attempts/${att!.id}/finish`, { method: 'POST' }))
    if (r) setAtt(r)
  }

  const isLast = idx === att.total - 1
  const t = q.type ?? 'mcq'
  return (
    <section className="card question" aria-label={`Question ${idx + 1} of ${att.total}`}>
      <div className="question-top">
        <span>{att.kind === 'reteach' ? 'Reteach quiz' : 'Formative quiz'} · {att.clo_code} · Question {idx + 1} of {att.total}</span>
        <span className="save-status"><CheckCircle2 className="icon-inline" aria-hidden="true" />{att.answered} of {att.total} saved</span>
      </div>
      <Bar value={(100 * answeredNow) / att.total} label="Questions answered" />
      <nav className="qnav" aria-label="Questions">
        {att.questions.map((x, i) => (
          <button key={x.id} type="button" className={`qnav-btn${i === idx ? ' is-current' : ''}${has(x.id === q.id ? draft : x.response) ? ' is-done' : ''}`}
            aria-current={i === idx ? 'step' : undefined} onClick={() => go(i)} disabled={act.busy}>
            {i + 1}<span className="sr-only">{has(x.id === q.id ? draft : x.response) ? ' (answered)' : ' (not answered)'}</span>
          </button>
        ))}
      </nav>
      <p className="qtype-tag">{TYPE_LABEL[t]}</p>
      <div className="question-stem"><Markdown>{q.stem}</Markdown></div>
      {att.kind === 'reteach' && q.hints_available && !q.response && QuizExtras.hint?.(att.id, q)}
      <QuestionInput q={q} value={draft} onChange={(r) => setDrafts({ ...drafts, [q.id]: r })} />
      <p className="small muted m-0">{att.kind === 'reteach' ? 'Your answers are saved as you go.' : 'Your answers are saved as you go and are marked when you submit — you can change them until then.'}</p>
      <Alert>{act.error}</Alert>
      <div className="question-nav">
        <button className="secondary" disabled={idx === 0 || act.busy} onClick={() => go(idx - 1)}>Previous</button>
        <div className="row">
          {!isLast && <button className={answeredNow === att.total ? 'secondary' : undefined} disabled={act.busy} aria-busy={act.busy || undefined} onClick={() => go(idx + 1)}>{has(draft) ? 'Save and next' : 'Next'}</button>}
          {(isLast || answeredNow === att.total) && (
            <button onClick={finish} disabled={act.busy || answeredNow < att.total} aria-busy={act.busy || undefined}>Submit quiz</button>
          )}
        </div>
      </div>
      {isLast && answeredNow < att.total && <p className="small muted m-0 mt-2">Answer all {att.total} questions to submit ({att.total - answeredNow} left). Use the numbers above to jump to a question.</p>}
    </section>
  )
}

/** The marked questions with the student's answer, the right answer and why. */
export function QuestionReviewList({ questions }: { questions: QQ[] }) {
  return (
    <ol className="qreview">
      {questions.map((q, n) => {
        const ans = q.answer
        const t = q.type ?? 'mcq'
        return (
          <li key={q.id} className={`kcard qreview-item ${ans?.correct ? 'is-right' : 'is-wrong'}`}>
            <div className="kcard-body">
              <p className="qreview-head">
                <span>Question {n + 1} <span className="muted">· {TYPE_LABEL[t]}</span></span>
                {ans ? (ans.correct ? <span className="tone-good">Correct</span> : <span className="tone-bad">Not quite</span>) : <span className="muted">Not answered</span>}
              </p>
              <Markdown>{q.stem}</Markdown>
              {t === 'mcq' ? (
                <ul className="qreview-options">
                  {q.options.map((o, i) => {
                    const isCorrect = ans?.correct_index === i
                    const isChosen = ans?.chosen === i
                    return (
                      <li key={i} className={`${isCorrect ? 'is-correct' : ''}${isChosen && !isCorrect ? ' is-chosen-wrong' : ''}`}>
                        <span className="qreview-letter" aria-hidden="true">{LETTER(i)}</span>
                        <span className="qreview-text"><Markdown inline>{o}</Markdown></span>
                        {isCorrect && <span className="qreview-tag"><Check aria-hidden="true" />{isChosen ? 'Your answer · correct' : 'Correct answer'}</span>}
                        {isChosen && !isCorrect && <span className="qreview-tag"><X aria-hidden="true" />Your answer</span>}
                      </li>
                    )
                  })}
                </ul>
              ) : ans && (
                <dl className="qreview-written">
                  <div className={ans.correct ? 'is-correct' : 'is-chosen-wrong'}><dt>Your answer</dt><dd>{ans.text || '—'}</dd></div>
                  {!(ans.correct && t === 'true_false') && <div className="is-correct"><dt>{t === 'short_answer' ? 'Model answer' : 'Correct answer'}</dt><dd>{ans.model_answer}</dd></div>}
                  {ans.feedback && <div><dt>Feedback</dt><dd>{ans.feedback}</dd></div>}
                </dl>
              )}
              {ans?.explanation && <div className="qreview-why"><strong>Why:</strong> <Markdown>{ans.explanation}</Markdown></div>}
              {ans && (ans.hints_used ?? 0) > 0 && <p className="caption">Hints used: {ans.hints_used}</p>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/** Shown after submission: the score, every answer with the right answer and why, then the debrief with the tutor. */
function QuizResult({ att, backTo, courseId }: { att: Attempt; backTo: string; courseId: string }) {
  const correct = att.correct ?? att.questions.filter((q) => q.answer?.correct).length
  const wrong = att.total - correct
  const m = att.mastery
  // the path decides what follows a quiz below the target: re-teaching (remedial) or another quiz (repeat)
  const reteach = !!m && !m.attained && att.kind === 'formative' && (!att.adaptation || ['remedial', 'review_fail'].includes(att.adaptation.decision))
  return (
    <div className="quiz-result">
      <section className="card" aria-labelledby="quiz-result">
        <h2 id="quiz-result" className="mt-0">Your result for {att.clo_code}: {correct} of {att.total} correct ({Math.round(att.score_pct ?? 0)}%)</h2>
        {m && <>
          <Bar value={m.mastery_pct} threshold={m.threshold} label={`${att.clo_code} mastery`} />
          <p>Your mastery of {att.clo_code} is now <strong>{m.mastery_pct}%</strong> (best of your last {m.attempts_counted} attempt{m.attempts_counted > 1 ? 's' : ''}). The mastery target is {m.threshold}%.</p>
          <p><Badge value={m.attained ? 'attained' : 'below'} /></p>
          {m.attained ? <Alert kind="success" title="Outcome mastered">Well done — you reached the target for {att.clo_code}.</Alert>
            : <Alert kind="info" title="Not mastered yet">Talk your mistakes through with your tutor below. {reteach ? 'Then this outcome is re-taught, starting from what you tell your tutor, and you get new questions.' : 'Then try again with new questions.'}</Alert>}
        </>}
        {att.adaptation && <Alert kind="info" title="What happens next">{att.adaptation.message} <span className="small muted">({att.adaptation.rule})</span></Alert>}
      </section>
      <details className="card result-answers" open={wrong > 0 && wrong <= 4}>
        <summary><h3 className="m-0 inline">Your answers ({correct} correct, {wrong} to review)</h3></summary>
        <QuestionReviewList questions={att.questions} />
      </details>
      <QuizDebrief attemptId={att.id} />
      <div className="card-actions">
        {reteach && <Link className="btn" to={`/learn/${courseId}/reteach/${att.clo_id}`}>Go to reteaching for {att.clo_code}</Link>}
        <Link className={`btn${reteach ? ' secondary' : ''}`} to={backTo}>{m && !m.attained && !reteach ? 'Back to my path (new quiz waiting)' : 'Back to my learning path'}</Link>
      </div>
    </div>
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
      {clo && <p className="muted quiz-outcome">{clo.description}</p>}
      {started ? (
        <QuizRunner courseId={courseId} backTo={`/learn/${courseId}/path`} start={() => api<Attempt>('/quiz/start', { body: { course_id: Number(courseId), clo_id: Number(cloId), step_id: step ? Number(step) : null } })} />
      ) : (
        <>
          <div className="card">
            <p className="m-0">This quiz has <strong>10 questions</strong>: multiple choice plus 3–5 true/false, fill-in-the-blank or short written answers. Every question was checked by your instructor, and every question counts toward your mastery.</p>
            <ul className="small muted">
              <li>There are no hints in this quiz: show what you know.</li>
              <li>Your answers are saved as you go and you can change them until you submit.</li>
              <li>You see your full result at the end, then your tutor goes through any mistakes with you.</li>
            </ul>
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
