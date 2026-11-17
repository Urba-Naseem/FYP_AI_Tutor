/** After a quiz: the AI tutor goes through each wrong answer like a teacher — asks what the student was thinking,
 *  works out why it went wrong, re-explains the idea and checks understanding. What it learns shapes reteaching. */
import { GraduationCap, Send, SkipForward } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Button } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, Loading, useAction, useLoad } from '../../components/ui'

type Msg = { role: 'teacher' | 'student'; text: string; q: number | null }
type Finding = { q: number; n: number; diagnosis: string; note: string; label: string }
type Debrief = { id: number; status: 'active' | 'done'; messages: Msg[]; total: number; position: number; current: number | null; findings: Finding[] }

const STARTERS = ['I guessed.', 'I mixed it up with something similar.', "I wasn't sure what the question was asking.", 'I thought my answer was right because…']

export function QuizDebrief({ attemptId }: { attemptId: number }) {
  const d0 = useLoad(() => api<Debrief>(`/quiz/attempts/${attemptId}/debrief`), [attemptId])
  const [d, setD] = useState<Debrief | null>(null)
  const [text, setText] = useState('')
  const act = useAction()
  const end = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const cur = d ?? d0.data
  useEffect(() => { if (d) end.current?.scrollIntoView({ block: 'nearest' }) }, [d])

  if (d0.loading && !cur) return <section className="card debrief"><Loading label="Your tutor is looking at your answers…" /></section>
  if (!cur) return <section className="card debrief"><Alert onRetry={d0.reload}>{d0.error}</Alert></section>

  const send = async (msg: string) => {
    const m = msg.trim()
    if (!m) return
    const r = await act.run(() => api<Debrief>(`/quiz/attempts/${attemptId}/debrief/reply`, { body: { message: m } }))
    if (r) { setD(r); setText('') }
  }
  const skip = async () => { const r = await act.run(() => api<Debrief>(`/quiz/attempts/${attemptId}/debrief/skip`, { method: 'POST' })); if (r) setD(r) }
  const firstReply = cur.current !== null && !cur.messages.some((m) => m.role === 'student' && m.q === cur.current)

  return (
    <section className="card debrief" aria-labelledby="debrief-title">
      <header className="debrief-head">
        <span className="tutor-avatar" aria-hidden="true"><GraduationCap className="icon" /></span>
        <div className="min-w-0">
          <h2 id="debrief-title" className="m-0">Let's talk about your answers</h2>
          <p className="small muted m-0">{cur.total === 0 ? 'Nothing to go over.' : cur.status === 'done' ? `We went through all ${cur.total} question${cur.total === 1 ? '' : 's'} you missed.`
            : `Question ${cur.position + 1} of ${cur.total} to talk through · tell your tutor honestly how you were thinking`}</p>
        </div>
      </header>
      <div className="debrief-thread" aria-live="polite">
        {cur.messages.map((m, i) => m.role === 'teacher' ? (
          <div key={i} className="debrief-msg is-teacher"><span className="tutor-avatar" aria-hidden="true"><GraduationCap className="icon" /></span><div className="debrief-bubble"><Markdown>{m.text}</Markdown></div></div>
        ) : (
          <div key={i} className="debrief-msg is-student"><div className="debrief-bubble">{m.text}</div></div>
        ))}
        {act.busy && <p className="spinner" role="status">Your tutor is thinking…</p>}
        <div ref={end} />
      </div>
      {cur.status === 'done' ? (
        cur.findings.length > 0 && (
          <div className="debrief-findings">
            <h3 className="m-0">What we found</h3>
            <ul>{cur.findings.map((f) => <li key={f.q}><strong>Question {f.n}:</strong> {f.label}{f.note && f.note !== 'skipped in the debrief' ? ` — ${f.note}` : ''}</li>)}</ul>
          </div>
        )
      ) : (
        <form className="debrief-form" onSubmit={(e) => { e.preventDefault(); send(text) }}>
          {firstReply && (
            <div className="debrief-starters" role="group" aria-label="Start your reply with">
              {STARTERS.map((s) => <button key={s} type="button" className="pill" onClick={() => { setText(s.endsWith('…') ? s.slice(0, -1) + ' ' : s); box.current?.focus() }}>{s}</button>)}
            </div>
          )}
          <label htmlFor="debrief-text" className="sr-only">Your reply to your tutor</label>
          <textarea id="debrief-text" ref={box} rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Tell your tutor what you were thinking…"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text) } }} />
          <div className="debrief-actions">
            <Button type="button" variant="ghost" size="sm" icon={<SkipForward />} onClick={skip} disabled={act.busy}>Skip this one</Button>
            <Button type="submit" size="sm" icon={<Send />} loading={act.busy} disabled={!text.trim()}>Reply</Button>
          </div>
        </form>
      )}
      <Alert>{act.error}</Alert>
    </section>
  )
}
