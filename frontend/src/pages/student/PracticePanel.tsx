/** Practice questions (ungraded, AI-written for this student and — on a lecture page — for that lecture's topic only).
 *  Each question has hints the student can open one at a time; answers and explanations appear only after the student
 *  answers. Each lecture keeps its own latest set. Practice never counts toward mastery and never uses quiz questions. */
import { CheckCircle2, Dumbbell, Lightbulb, RefreshCw, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Button, Card } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, useAction } from '../../components/ui'

type PQ = { index: number; stem: string; options: string[]; hints?: string[]; chosen?: number; correct?: boolean; correct_index?: number; explanation?: string }
type PSet = { id: number; questions: PQ[]; answered: number; correct: number; total: number }
const LETTER = 'ABCD'

export function PracticePanel({ courseId, cloId, topicId, cloCode, title = 'Practice questions' }: { courseId: string; cloId: number; topicId?: number; cloCode: string; title?: string }) {
  const [set, setSet] = useState<PSet | null>(null)
  const [shown, setShown] = useState<Record<number, number>>({})  // hints opened per question
  const [restoring, setRestoring] = useState(true)
  const act = useAction()
  // restore this lecture's latest set (each topic has its own)
  useEffect(() => {
    let live = true
    setSet(null); setShown({}); setRestoring(true)
    api<PSet | null>(`/practice/${courseId}/clos/${cloId}/latest${topicId ? `?topic_id=${topicId}` : ''}`)
      .then((s) => { if (live) setSet(s) }).catch(() => { /* nothing to restore */ }).finally(() => { if (live) setRestoring(false) })
    return () => { live = false }
  }, [courseId, cloId, topicId])
  const start = async () => {
    const r = await act.run(() => api<PSet>(`/practice/${courseId}/clos/${cloId}`, { body: { topic_id: topicId ?? null } }))
    if (r) { setSet(r); setShown({}) }
  }
  const answer = async (q: PQ, choice: number) => {
    if (!set || q.chosen != null) return
    const r = await act.run(() => api<PSet>(`/practice/sets/${set.id}/answer`, { body: { index: q.index, choice } }))
    if (r) setSet(r)
  }
  const done = set && set.answered === set.total
  return (
    <Card title={title} subtitle={`${topicId ? 'Questions on this lecture' : `Warm up for ${cloCode}`} · not marked, does not affect your mastery · hints available`}
      action={set ? <Button variant="secondary" size="sm" icon={<RefreshCw />} loading={act.busy} onClick={start}>New questions</Button> : undefined}>
      {!set ? (
        <div className="practice-start">
          <p className="m-0">{topicId ? 'Try a few questions written for this lecture at your level.' : 'Try a few questions written for your level before you take the quiz.'} Stuck? Open a hint. You see the answer and why straight after each one.</p>
          <Button icon={<Dumbbell />} loading={act.busy || restoring} onClick={start}>{act.busy ? 'Writing your questions…' : 'Start practice'}</Button>
        </div>
      ) : (
        <>
          <p className="small muted m-0" aria-live="polite">{set.answered} of {set.total} answered · {set.correct} correct</p>
          <ol className="qreview">
            {set.questions.map((q) => {
              const answered = q.chosen != null
              const hints = q.hints ?? []
              const open = shown[q.index] ?? 0
              return (
                <li key={`${set.id}-${q.index}`} className={`kcard qreview-item ${answered ? (q.correct ? 'is-right' : 'is-wrong') : ''}`}>
                  <p className="qreview-head"><span>Question {q.index + 1}</span>{answered && (q.correct ? <span className="tone-good">Correct</span> : <span className="tone-bad">Not quite</span>)}</p>
                  <Markdown>{q.stem}</Markdown>
                  {!answered && hints.length > 0 && (
                    <div className="practice-hints" aria-live="polite">
                      {hints.slice(0, open).map((h, i) => <p key={i} className="practice-hint"><Lightbulb aria-hidden="true" /><span><strong>Hint {i + 1}:</strong> {h}</span></p>)}
                      {open < hints.length && (
                        <button type="button" className="ghost small" onClick={() => setShown({ ...shown, [q.index]: open + 1 })}>
                          <Lightbulb aria-hidden="true" />{open ? 'Another hint' : 'Show a hint'}<span className="sr-only"> for question {q.index + 1}</span>
                        </button>
                      )}
                    </div>
                  )}
                  <ul className="qreview-options" role="list">
                    {q.options.map((o, i) => {
                      const isCorrect = answered && q.correct_index === i
                      const isWrong = answered && q.chosen === i && !isCorrect
                      return (
                        <li key={i} className={`${isCorrect ? 'is-correct' : ''}${isWrong ? ' is-chosen-wrong' : ''}`}>
                          <span className="qreview-letter" aria-hidden="true">{LETTER[i]}</span>
                          {answered ? <span className="qreview-text"><Markdown inline>{o}</Markdown></span> : (
                            <button type="button" className="practice-option" onClick={() => answer(q, i)} disabled={act.busy}>
                              <span className="sr-only">Answer {LETTER[i]}: </span><Markdown inline>{o}</Markdown>
                            </button>
                          )}
                          {isCorrect && <span className="qreview-tag"><CheckCircle2 aria-hidden="true" />{q.chosen === i ? 'Your answer · correct' : 'Correct answer'}</span>}
                          {isWrong && <span className="qreview-tag"><XCircle aria-hidden="true" />Your answer</span>}
                        </li>
                      )
                    })}
                  </ul>
                  {answered && q.explanation && <div className="qreview-why"><strong>Why:</strong> <Markdown>{q.explanation}</Markdown></div>}
                </li>
              )
            })}
          </ol>
          {done && <Alert kind={set.correct / set.total >= 0.6 ? 'success' : 'info'} title={`${set.correct} of ${set.total} correct`}>
            {set.correct / set.total >= 0.6 ? 'Nice — you look ready for the quiz. ' : 'Read the explanations above, then try a new set. '}Practice questions are never used in your quizzes.
          </Alert>}
        </>
      )}
      <Alert>{act.error}</Alert>
    </Card>
  )
}
