import { CheckCircle2, ClipboardList, Clock, CloudOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import Markdown from '../../components/Markdown'
import { Alert, Badge, Bar, Crumbs, EmptyState, Loading, useAction, useLoad } from '../../components/ui'
import { PathSteps, type Path } from './PathPage'

type Q = { id: number; stem: string; options: string[]; clo_id: number }
type State = { attempt_id: number; status: string; answered: number; total: number; question?: Q | null; draft_choice?: number | null; last_saved_at: string; results?: Res[] }
type Res = { clo_id: number; clo_code: string; description: string; score_pct: number; level: string; answered: number; correct: number }
export type Status = { published: boolean; preparing?: boolean; attempt: State | null; results: Res[] | null; needed?: boolean; reason?: string }

/** Semester 2+ course with a completed prerequisite: no diagnostic, the path is built from the prerequisite result. */
export function PrerequisitePathCard({ courseId, reason, onBuilt }: { courseId: string; reason?: string; onBuilt: () => void }) {
  const act = useAction()
  async function build() {
    const r = await act.run(() => api(`/paths/me/${courseId}/generate`, { method: 'POST' }), 'Learning path built')
    if (r) onBuilt()
  }
  return (
    <section className="card next-step" aria-labelledby="prereq-title">
      <p className="eyebrow">Your next step</p>
      <h2 id="prereq-title" className="mt-0">No diagnostic needed</h2>
      <p className="card-desc">{reason}</p>
      <Alert>{act.error}</Alert>
      <div className="card-actions"><button onClick={build} aria-busy={act.busy || undefined} disabled={act.busy}>Build my learning path</button></div>
    </section>
  )
}

export function ReadinessTable({ results }: { results: Res[] }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
      <thead><tr><th>Outcome</th><th>Readiness</th><th>Score</th></tr></thead>
      <tbody>{results.map((r) => (
        <tr key={r.clo_id}>
          <td><strong>{r.clo_code}</strong> <span className="small muted">{r.description}</span></td>
          <td><Badge value={r.level} /></td>
          <td className="min-w-md"><Bar value={r.score_pct} threshold={70} label={`${r.clo_code} diagnostic score`} /><span className="small muted">{r.correct} of {r.answered} correct</span></td>
        </tr>
      ))}</tbody>
    </table></div>
  )
}

/** The path generated from the diagnostic, shown straight after the results. */
function NewPath({ courseId }: { courseId: string }) {
  const p = useLoad(() => api<Path | null>(`/paths/me/${courseId}`), [courseId])
  if (p.loading && !p.data) return <Loading />
  if (!p.data) return p.error ? <Alert>We couldn't load your learning path: {p.error}</Alert> : null
  return (
    <>
      <h2>Your learning path</h2>
      <div className="card">
        <p className="mt-0">{p.data.summary}</p>
        <Link className="btn" to={`/learn/${courseId}/path`}>Start my learning path</Link>
      </div>
      <PathSteps path={p.data} courseId={courseId} quietNext />
    </>
  )
}

export default function DiagnosticPage() {
  const { courseId } = useParams()
  const st = useLoad(() => api<Status>(`/diagnostic/${courseId}/status`), [courseId])
  const [att, setAtt] = useState<State | null>(null)
  const [choice, setChoice] = useState<number | null>(null)
  const [saved, setSaved] = useState('')
  const [built, setBuilt] = useState(false)
  const act = useAction()
  const timer = useRef<number | undefined>(undefined)

  // While the diagnostic is being written (automation), check again every 10 seconds.
  useEffect(() => {
    if (!st.data?.preparing) return
    const t = window.setTimeout(() => st.reload(), 10000)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.data])

  useEffect(() => {
    if (st.data?.attempt?.status === 'in_progress') {
      setAtt(st.data.attempt)
      setChoice(st.data.attempt.draft_choice ?? null)
    }
  }, [st.data])

  // Autosave the draft choice; on failure show "Offline — will retry" and retry with back-off (same request).
  useEffect(() => () => window.clearTimeout(timer.current), [])
  function pick(i: number) {
    setChoice(i)
    setSaved('Saving…')
    window.clearTimeout(timer.current)
    const save = (tries: number) => {
      timer.current = window.setTimeout(async () => {
        try {
          await api(`/diagnostic/attempts/${att!.attempt_id}/draft`, { method: 'PUT', body: { item_id: att!.question!.id, choice: i } })
          setSaved('Saved')
        } catch {
          if (tries < 5) { setSaved('Offline — will retry'); save(tries + 1) }
          else setSaved('Not saved — check your connection, then choose your answer again')
        }
      }, tries === 0 ? 300 : Math.min(2000 * tries, 8000))
    }
    save(0)
  }

  async function start() {
    const r = await act.run(() => api<State>(`/diagnostic/${courseId}/start`, { method: 'POST' }))
    if (r) { setAtt(r); setChoice(r.draft_choice ?? null) }
  }
  async function submit() {
    if (choice === null || !att?.question) return
    window.clearTimeout(timer.current)
    const r = await act.run(() => api<State>(`/diagnostic/attempts/${att.attempt_id}/answer`, { body: { item_id: att.question!.id, choice } }))
    if (r) { setAtt(r); setChoice(null); setSaved(''); if (r.status === 'completed') st.reload() }
  }

  const crumbs = [{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: 'Course' }, { label: 'Diagnostic test' }]
  if (st.loading && !st.data) return <><Crumbs items={crumbs} /><h1>Diagnostic test</h1><Loading variant="cards" /></>
  const results = att?.results ?? st.data?.results
  if (st.data?.needed === false) {
    return (
      <>
        <Crumbs items={crumbs} />
        <h1>Diagnostic test</h1>
        {built ? <NewPath courseId={courseId!} /> : <PrerequisitePathCard courseId={courseId!} reason={st.data.reason} onBuilt={() => setBuilt(true)} />}
      </>
    )
  }
  const SaveIcon = saved === 'Saved' ? CheckCircle2 : saved.startsWith('Offline') || saved.startsWith('Not saved') ? CloudOff : Clock
  return (
    <>
      <Crumbs items={crumbs} />
      <h1>Diagnostic test</h1>
      {st.error ? <Alert onRetry={st.reload}>{st.error}</Alert> : <Alert>{act.error}</Alert>}
      {results ? (
        <>
          <Alert kind="success" title="Diagnostic complete">Your personalised learning path has been built from these results — see it below.</Alert>
          <ReadinessTable results={results} />
          <NewPath courseId={courseId!} />
        </>
      ) : !st.data?.published ? (
        !st.error && (st.data?.preparing ? (
          <EmptyState icon={ClipboardList} title="Your diagnostic is being written" action={<button className="secondary" onClick={st.reload}>Check again</button>}>
            Questions are being written for every learning outcome; your instructor then approves them. This page checks again automatically.
          </EmptyState>
        ) : <EmptyState icon={ClipboardList} title="The diagnostic isn't available yet">It will appear here once your instructor has approved the questions. Check back later.</EmptyState>)
      ) : !att ? (
        <section className="card question" aria-labelledby="diag-intro">
          <h2 id="diag-intro" className="mt-0">Before you start</h2>
          <p className="card-desc">This short test finds what you already know for each course learning outcome. It adapts to your answers, there is no time limit, and your progress is saved automatically — you can close the page and continue later. It does not count towards your grade.</p>
          <div className="card-actions"><button onClick={start} aria-busy={act.busy || undefined} disabled={act.busy}>{st.data.attempt ? 'Resume diagnostic' : 'Start diagnostic'}</button></div>
        </section>
      ) : att.question ? (
        <section className="card question" aria-label={`Question ${att.answered + 1} of ${att.total}`}>
          <div className="question-top">
            <span>Question {att.answered + 1} of {att.total}</span>
            <span className="save-status" aria-live="polite">{saved && <><SaveIcon className="icon-inline" aria-hidden="true" />{saved}</>}</span>
          </div>
          <Bar value={(100 * att.answered) / Math.max(att.total, 1)} label="Diagnostic progress" />
          <fieldset>
            <legend className="sr-only">Choose one answer</legend>
            <div className="question-stem"><Markdown>{att.question.stem}</Markdown></div>
            {att.question.options.map((o, i) => (
              <label key={i} className="option">
                <input type="radio" name="opt" checked={choice === i} onChange={() => pick(i)} />
                <span>{String.fromCharCode(65 + i)}. {o}</span>
              </label>
            ))}
          </fieldset>
          {/* No "Previous": the diagnostic is adaptive — each question depends on the previous answer. */}
          <div className="question-nav">
            <span className="small muted">Answers are saved as you choose them.</span>
            <button disabled={choice === null || act.busy} aria-busy={act.busy || undefined} onClick={submit}>{att.answered + 1 === att.total ? 'Submit diagnostic' : 'Next question'}</button>
          </div>
        </section>
      ) : <Loading />}
    </>
  )
}
