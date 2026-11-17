import { Check, Copy, Flag, Info, LifeBuoy, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { Fragment, useId, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { askText } from './dialogApi'
import { Alert, useAction } from './ui'

type Citation = { ref: string; source_title: string; heading: string | null; excerpt: string }
type Answer = { answer: string; citations: Citation[]; mode: string; ai_call_id: number | null }
type Turn = { id: number; question: string; answer: Answer; flagged: boolean }

const MODE_LABEL: Record<string, string> = {
  live: 'Based on your course outcomes and suggested reading',
  curriculum: 'Based on your course learning outcomes',
  offline: 'Offline mode · limited answer',
  refused: 'Outside this course’s learning outcomes',
  blocked: 'Answer protected',
}

/** Render answer text with inline [S1] markers as citation pills linking to the source list. */
function CitedText({ text, citations, anchor }: { text: string; citations: Citation[]; anchor: string }) {
  const known = new Set(citations.map((c) => c.ref))
  const parts = text.split(/(\[S\d+\])/g)
  return (
    <p>
      {parts.map((p, i) => {
        const ref = p.match(/^\[(S\d+)\]$/)?.[1]
        if (ref && known.has(ref)) {
          const c = citations.find((x) => x.ref === ref)!
          return <a key={i} className="cite" href={`#${anchor}-${ref}`} title={c.source_title}>{ref}</a>
        }
        return <Fragment key={i}>{p}</Fragment>
      })}
    </p>
  )
}

/** Course-scoped tutor: answers within the course's learning outcomes (citing suggested reading when it matches) and can be
 *  flagged for review. For students it uses Socratic nudging: a hint and a guiding question first, building on their
 *  replies, and a full explanation once they have reasoned it through or say they are stuck. */
export default function AskTutor({ courseId, context, socratic = true }: { courseId: number; context?: string; socratic?: boolean }) {
  const [q, setQ] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [pending, setPending] = useState('')
  const [copied, setCopied] = useState<number | null>(null)
  const act = useAction()
  const uid = useId().replace(/:/g, '')

  async function send(question: string) {
    if (!question) return
    setPending(question)
    setQ('')
    const history = turns.slice(-4).map((t) => ({ question: t.question, answer: t.answer.answer }))
    const r = await act.run(() => api<Answer>('/ai/ask', { body: { course_id: courseId, question: context ? `${question} (context: ${context})` : question, history } }))
    setPending('')
    if (r) setTurns((t) => [...t, { id: t.length + 1, question, answer: r, flagged: false }])
    else setQ(question)
  }
  function ask(e: FormEvent) {
    e.preventDefault()
    send(q.trim())
  }

  async function flag(turn: Turn) {
    const reason = await askText({
      title: 'Report a problem with this answer',
      message: 'Your instructor will review this answer and your note. The answer stays visible to you.',
      inputLabel: 'What is wrong with this answer?',
      confirmLabel: 'Send report',
    })
    if (!reason || !turn.answer.ai_call_id) return
    if (await act.run(() => api(`/ai/calls/${turn.answer.ai_call_id}/flag`, { body: { reason } })))
      setTurns((t) => t.map((x) => (x.id === turn.id ? { ...x, flagged: true } : x)))
  }

  async function copy(turn: Turn) {
    try {
      await navigator.clipboard.writeText(turn.answer.answer)
      setCopied(turn.id)
      setTimeout(() => setCopied((c) => (c === turn.id ? null : c)), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  const thinking = act.busy && !!pending

  return (
    <section className={`tutor${turns.length || pending ? ' has-turns' : ''}`} aria-labelledby={`${uid}-title`}>
      <header className="tutor-head">
        <div className={`tutor-avatar${thinking ? ' thinking' : ''}`} aria-hidden="true"><Sparkles className="icon" /></div>
        <div>
          <h2 id={`${uid}-title`}>Ask the tutor</h2>
          <p className="small muted">{socratic ? "Your tutor guides you with hints and questions first, so you work it out yourself. Say you're stuck any time." : "Answers stay within your course's learning outcomes."}</p>
        </div>
      </header>

      <div className="tutor-thread" aria-live="polite">
        {turns.map((t) => {
          const anchor = `${uid}-t${t.id}`
          const refused = t.answer.mode === 'refused'
          return (
            <div key={t.id} className="turn">
              <div className="msg-user">{t.question}</div>
              <article className="msg-ai" aria-label="Tutor answer">
                <div className="tutor-avatar" aria-hidden="true"><Sparkles className="icon" /></div>
                <div className={`msg-ai-card${refused ? ' refused' : ''}`}>
                  <p className="msg-meta">{refused ? <Info className="icon-inline" aria-hidden="true" /> : <ShieldCheck className="icon-inline" aria-hidden="true" />}{MODE_LABEL[t.answer.mode] ?? t.answer.mode}</p>
                  <CitedText text={t.answer.answer} citations={t.answer.citations} anchor={anchor} />
                  {t.answer.citations.length > 0 && (
                    <ol className="sources" aria-label="Sources">
                      {t.answer.citations.map((c) => (
                        <li key={c.ref} id={`${anchor}-${c.ref}`}>
                          <span className="cite">{c.ref}</span>
                          <span>
                            <strong>{c.source_title}</strong>{c.heading ? ` — ${c.heading}` : ''}
                            <span className="muted"> · {c.excerpt}…</span>
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                  <div className="msg-actions">
                    {socratic && t.id === turns.length && !refused && t.answer.mode !== 'offline' && /\?\s*$/.test(t.answer.answer.trim()) && (
                      <button type="button" className="ghost small" onClick={() => send("I'm stuck - please explain it to me.")} disabled={act.busy}><LifeBuoy aria-hidden="true" />I'm stuck — explain it</button>
                    )}
                    <button type="button" className="ghost small" onClick={() => copy(t)}><Copy aria-hidden="true" />{copied === t.id ? 'Copied' : 'Copy answer'}</button>
                    {t.answer.ai_call_id && (t.flagged
                      ? <span className="small muted"><Check className="icon-inline" aria-hidden="true" /> Thanks — your instructor will review this answer.</span>
                      : <button type="button" className="ghost small" onClick={() => flag(t)}><Flag aria-hidden="true" />Report a problem</button>)}
                  </div>
                </div>
              </article>
            </div>
          )
        })}
        {pending && (
          <div className="turn">
            <div className="msg-user">{pending}</div>
            <p className="spinner" role="status">The tutor is thinking…</p>
          </div>
        )}
      </div>

      <Alert>{act.error}</Alert>

      <form onSubmit={ask} className="composer">
        <input aria-labelledby={`${uid}-title`} value={q} onChange={(e) => setQ(e.target.value)} placeholder={turns.length ? 'Reply with your thinking, or ask something new…' : 'Ask about this material…'} required />
        <button disabled={act.busy} aria-busy={act.busy || undefined}>{act.busy ? 'Thinking…' : <><Send aria-hidden="true" />Send question</>}</button>
      </form>
    </section>
  )
}
