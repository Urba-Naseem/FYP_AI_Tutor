import { BookOpen, Flag, NotebookPen, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import AskTutor from '../../components/AskTutor'
import { askText } from '../../components/dialogApi'
import Markdown from '../../components/Markdown'
import { outlineOf } from '../../components/slug'
import { Alert, Badge, Crumbs, EmptyState, Loading, useAction, useLoad } from '../../components/ui'
import { STEP_LINKS } from './PathPage'
import { TopicList } from './TopicPage'

type Unit = {
  id: number; number: number; title: string
  clos: { id: number; code: string; description: string }[]
  lesson: { version_id: number; version_no: number; title: string; body_md: string } | null
  alternates: { clo_id: number; title: string; version_id: number; body_md: string }[]
  personal?: boolean
}

// Path steps that open this page.
// a Study step for a course topic opens the topic page; older CLO-level steps open the unit page
STEP_LINKS.learn = (c, s) => (s.meta?.topic_id ? `/learn/${c}/topic/${s.meta.topic_id}?step=${s.id}` : `/learn/${c}/unit/${s.unit_id}?step=${s.id}`)
STEP_LINKS.remedial = (c, s) => `/learn/${c}/unit/${s.unit_id}?step=${s.id}&alt=${s.clo_id}`

/** The page already shows the unit title, so drop a duplicate leading "# Title" from the lesson. */
function withoutLeadingTitle(md: string) {
  return md.replace(/^\s*#\s+[^\n]*\n+/, '')
}

function plainText(md: string) {
  return md.replace(/```[\s\S]*?```/g, ' code example. ').replace(/\$\$?[^$]+\$\$?/g, ' formula. ').replace(/[#*_>`|[\]()-]/g, ' ')
}

/** Read the material aloud with the browser's speech synthesis (accessibility aid). */
function ReadAloud({ text }: { text: string }) {
  const [speaking, setSpeaking] = useState(false)
  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window
  useEffect(() => () => { if (supported) window.speechSynthesis.cancel() }, [supported])
  if (!supported) return null
  return (
    <button className="secondary small" aria-pressed={speaking} onClick={() => {
      if (speaking) { window.speechSynthesis.cancel(); setSpeaking(false); return }
      const u = new SpeechSynthesisUtterance(plainText(text))
      u.onend = () => setSpeaking(false)
      window.speechSynthesis.speak(u)
      setSpeaking(true)
    }}>{speaking ? 'Stop reading' : 'Read aloud'}</button>
  )
}

function ReportProblem({ versionId }: { versionId: number }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ category: 'error', message: '', excerpt: '' })
  const act = useAction()
  if (!open) return <button className="ghost" onClick={() => { setForm({ ...form, excerpt: window.getSelection()?.toString().slice(0, 300) ?? '' }); setOpen(true) }}><Flag aria-hidden="true" />Report a problem</button>
  return (
    <section className="card" aria-labelledby="report-title">
      <h2 id="report-title" className="mt-0">Report a problem with this material</h2>
      {act.message ? <Alert kind="success" title="Report sent">{act.message}</Alert> : (
        <>
          <div className="field"><label htmlFor="rc">Type</label>
            <select id="rc" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              <option value="error">Mistake in the content</option><option value="unclear">Hard to understand</option><option value="accessibility">Accessibility problem</option><option value="other">Other</option>
            </select></div>
          {form.excerpt && <p className="small">Selected text: <em>{form.excerpt}</em></p>}
          <div className="field"><label htmlFor="rm">What is wrong?</label><textarea id="rm" value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} /></div>
          <Alert>{act.error}</Alert>
          <div className="card-actions">
            <button className="secondary" onClick={() => setOpen(false)}>Cancel</button>
            <button aria-busy={act.busy || undefined} disabled={act.busy} onClick={() => act.run(() => api(`/learn/versions/${versionId}/report`, { body: form }), 'Thank you — your instructor has been notified.')}>Send report</button>
          </div>
        </>
      )}
    </section>
  )
}

/** "On this page" outline: beside the lesson on desktop, collapsible above it on mobile. */
function Outline({ md }: { md: string }) {
  const items = outlineOf(md)
  const [open] = useState(() => typeof window.matchMedia !== 'function' || window.matchMedia('(min-width: 1024px)').matches)
  if (items.length < 2) return null
  return (
    <nav className="outline" aria-label="On this page">
      <details open={open}>
        <summary>On this page</summary>
        <ol>{items.map((h) => <li key={h.id} className={h.level === 3 ? 'sub' : undefined}><a href={`#${h.id}`}>{h.text}</a></li>)}</ol>
      </details>
    </nav>
  )
}

type Personal = { clo_id: number; clo_code: string; description: string; level: string; level_label: string; reason: string; origin: string; ai_call_id: number | null; cited_sources: boolean; lecture_md: string; notes_md: string }

const LEVEL_TONE: Record<string, string> = { foundations: 'warn', standard: 'info', extension: 'ok' }

/** One learner's own lecture + notes for one CLO, written by the AI for their diagnostic / mastery level. */
function PersonalLecture({ courseId, clo }: { courseId: string; clo: { id: number; code: string; description: string } }) {
  const [refresh, setRefresh] = useState(0)
  const l = useLoad(() => api<Personal>(`/learn/${courseId}/clos/${clo.id}/lesson${refresh ? '?refresh=true' : ''}`), [courseId, clo.id, refresh])
  const act = useAction()
  const [flagged, setFlagged] = useState(false)
  const titleId = `clo-${clo.id}`
  async function report() {
    if (!l.data?.ai_call_id) return
    const reason = await askText({ title: 'Report a problem with this lecture', message: 'Your instructor will review this lecture and your note.', inputLabel: 'What is wrong?', confirmLabel: 'Send report' })
    if (reason && (await act.run(() => api(`/ai/calls/${l.data!.ai_call_id}/flag`, { body: { reason } }), 'Thank you — your instructor will review it.'))) setFlagged(true)
  }
  return (
    <section className="personal-lecture" aria-labelledby={titleId}>
      <h2 id={titleId}>{clo.code}: {clo.description}</h2>
      {l.loading && !l.data ? <Loading label="Writing your lecture… (this can take a few seconds)" /> : l.error ? <Alert onRetry={l.reload}>{l.error}</Alert> : l.data && (
        <>
          <p className="small muted">
            <Badge value={`Pitched for you: ${l.data.level_label}`} tone={LEVEL_TONE[l.data.level] ?? 'info'} /> based on your {l.data.reason}
            {l.data.origin === 'offline' && ' · AI offline, showing a study guide'}
          </p>
          <Markdown>{l.data.lecture_md}</Markdown>
          {l.data.notes_md && (
            <aside className="notes-card" aria-labelledby={`${titleId}-notes`}>
              <h3 id={`${titleId}-notes`}><NotebookPen className="icon-inline" aria-hidden="true" /> My notes</h3>
              <Markdown>{l.data.notes_md}</Markdown>
            </aside>
          )}
          <Alert>{act.error}</Alert>
          <div className="row mt-3">
            <button className="ghost small" disabled={l.loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw aria-hidden="true" />Explain it differently</button>
            {l.data.ai_call_id && (flagged ? <span className="small muted">Reported — thank you.</span> : <button className="ghost small" onClick={report}><Flag aria-hidden="true" />Report a problem</button>)}
          </div>
        </>
      )}
    </section>
  )
}

export default function UnitPage() {
  const { courseId = '', unitId } = useParams()
  const [params] = useSearchParams()
  const nav = useNavigate()
  const step = params.get('step')
  const altClo = Number(params.get('alt')) || null
  const u = useLoad(() => api<Unit>(`/learn/${courseId}/units/${unitId}`), [courseId, unitId])
  const act = useAction()
  const crumbs = (label: string) => <Crumbs items={[{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: 'Course' }, { to: `/learn/${courseId}/units`, label: 'Course material' }, { label }]} />
  if (u.loading && !u.data) return <>{crumbs('Unit')}<div className="lesson"><Loading /></div></>
  if (!u.data) return <>{crumbs('Unit')}<h1>Unit</h1><Alert onRetry={u.reload}>{u.error}</Alert></>
  const alt = altClo ? u.data.alternates.find((a) => a.clo_id === altClo) : null
  const shown = alt ? { title: alt.title, body_md: alt.body_md, version_id: alt.version_id } : u.data.lesson
  const body = shown ? withoutLeadingTitle(shown.body_md) : ''
  const quizTo = u.data.clos.length === 1 ? `/learn/${courseId}/quiz/${u.data.clos[0].id}` : `/learn/${courseId}/practice`
  // Personalised mode: one lecture per CLO written for this learner (a foundations step shows just its CLO).
  const personal = u.data.personal
  const clos = altClo ? u.data.clos.filter((o) => o.id === altClo) : u.data.clos
  const finish = (
    <div className="lesson-end">
      {shown && !personal ? <ReportProblem versionId={shown.version_id} /> : <span />}
      {step ? (
        <button aria-busy={act.busy || undefined} disabled={act.busy} onClick={async () => { if (await act.run(() => api(`/learn/steps/${step}/complete`, { method: 'POST' }))) nav(`/learn/${courseId}/path`) }}>I have studied this — continue my path</button>
      ) : (
        <Link className="btn" to={quizTo}>Take quiz</Link>
      )}
    </div>
  )
  return (
    <>
      {crumbs(`Unit ${u.data.number}`)}
      <div className={`lesson-layout${(personal ? clos.length > 1 : shown && outlineOf(body).length > 1) ? ' has-outline' : ''}`}>
        <article className="lesson" aria-labelledby="unit-title">
          <div className="page-head">
            <h1 id="unit-title">Unit {u.data.number}: {u.data.title}</h1>
            {shown && !personal && <ReadAloud text={shown.body_md} />}
          </div>
          <ul className="objectives" aria-label="In this unit you will">
            {u.data.clos.map((o) => <li key={o.id}><span className="badge">{o.code}</span><span>{o.description}</span></li>)}
          </ul>
          {personal ? (
            <>
              <Alert kind="info" title="Your personal lecture">Written for you from this unit's learning outcomes and pitched to your diagnostic and quiz results. It changes as you improve.</Alert>
              {clos.map((o) => <PersonalLecture key={o.id} courseId={courseId} clo={o} />)}
              {shown && (
                <details className="card mt-6">
                  <summary><strong>Course notes from your instructor</strong></summary>
                  <Markdown>{body}</Markdown>
                  <div className="mt-3"><ReportProblem versionId={shown.version_id} /></div>
                </details>
              )}
            </>
          ) : (
            <>
              {altClo && !alt && <Alert kind="info" title="Foundations explanation not published yet">Start with the main lesson below.</Alert>}
              {!shown ? (
                <EmptyState icon={BookOpen} title="Not published yet" action={<Link className="btn secondary" to={`/learn/${courseId}/units`}>Back to course material</Link>}>
                  The material for this unit has not been published yet.
                </EmptyState>
              ) : (
                <Markdown>{body}</Markdown>
              )}
            </>
          )}
          <Alert>{act.error}</Alert>
          {(personal || shown) && finish}
        </article>
        {personal ? (clos.length > 1 && (
          <nav className="outline" aria-label="On this page">
            <details open>
              <summary>On this page</summary>
              <ol>{clos.map((o) => <li key={o.id}><a href={`#clo-${o.id}`}>{o.code}: {o.description}</a></li>)}</ol>
            </details>
          </nav>
        )) : shown && <Outline md={body} />}
      </div>
      <AskTutor courseId={Number(courseId)} context={`Unit ${u.data.number}: ${u.data.title}`} />
    </>
  )
}

export function UnitListPage() {
  const { courseId = '' } = useParams()
  const units = useLoad(() => api<{ id: number; number: number; title: string; available: boolean; clos: { code: string }[] }[]>(`/learn/${courseId}/units`), [courseId])
  return (
    <>
      <Crumbs items={[{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: 'Course' }, { label: 'Course material' }]} />
      <h1>Course material</h1>
      <p className="muted">Every topic of the course, in teaching order. Each lesson is written for your level; topics marked "In your path" were picked from your diagnostic.</p>
      <h2>Topics</h2>
      <TopicList courseId={courseId} />
      <h2>Units</h2>
      {units.loading ? <Loading variant="table" /> : units.error ? <Alert onRetry={units.reload}>{units.error}</Alert> : !units.data?.length ? (
        <EmptyState icon={BookOpen} title="No units yet">Your instructor is still setting up this course — check back later.</EmptyState>
      ) : (
        <ul className="list-plain">
          {units.data.map((u) => (
            <li key={u.id} className="card">
              <div className="spread">
                <h2 className="m-0">{u.available ? <Link to={`/learn/${courseId}/unit/${u.id}`}>Unit {u.number}: {u.title}</Link> : <>Unit {u.number}: {u.title}</>}</h2>
                <Badge value={u.available ? 'published' : 'draft'} tone={u.available ? 'ok' : ''} />
              </div>
              <p className="card-desc m-0 mt-1">{u.clos.map((o) => o.code).join(', ')}{!u.available && ' · not published yet'}</p>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
