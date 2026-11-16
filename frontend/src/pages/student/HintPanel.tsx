import { Lightbulb } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Alert, Badge, useAction } from '../../components/ui'
import { QuizExtras, type QQ } from './QuizPage'

type Hint = { level: number; text: string; mode: string }

/** Progressive hints for the current quiz question (US-6.2). */
export default function HintPanel({ attemptId, question }: { attemptId: number; question: QQ }) {
  const [hints, setHints] = useState<Hint[]>([])
  const act = useAction()
  useEffect(() => {
    api<Hint[]>(`/quiz/attempts/${attemptId}/hints/${question.id}`).then(setHints).catch(() => setHints([]))
  }, [attemptId, question.id])
  const max = 3
  const request = (
    <button className="ghost small" disabled={act.busy} aria-busy={act.busy || undefined} onClick={async () => {
      const r = await act.run(() => api<Hint>(`/quiz/attempts/${attemptId}/hint`, { body: { item_id: question.id } }))
      if (r) setHints([...hints, r])
    }}><Lightbulb aria-hidden="true" />{act.busy ? 'Getting a hint… (this can take a few seconds)' : hints.length ? 'Request another hint' : 'Request hint'}</button>
  )
  // Hints never contain the answer; they sit in their own info panel, apart from answer feedback.
  return (
    <div aria-live="polite">
      {hints.length > 0 && (
        <div className="hint-panel">
          {hints.map((h) => (
            <p key={h.level} className="hint">
              <span className="hint-label"><Lightbulb className="icon-inline" aria-hidden="true" />Hint {h.level} of {max}</span>{' '}
              {h.mode !== 'socratic' && <Badge value={h.mode === 'static' ? 'instructor hint' : 'protected'} />}
              <br />{h.text}
            </p>
          ))}
          {hints.length < max ? request : <p className="small m-0">No more hints for this question — give it your best try.</p>}
        </div>
      )}
      {hints.length === 0 && request}
      <Alert>{act.error}</Alert>
    </div>
  )
}

QuizExtras.hint = (attemptId, q) => <HintPanel key={q.id} attemptId={attemptId} question={q} />
