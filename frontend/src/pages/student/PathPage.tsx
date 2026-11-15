import { BookOpen, Check, ChevronRight, ClipboardList, Compass, Layers, Lock, type LucideIcon, RefreshCw, Route, Zap } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { PathChart, Ring } from '../../components/charts'
import { Alert, Badge, Crumbs, EmptyState, fmtDate, Loading, useAction, useLoad } from '../../components/ui'

export type Step = { id: number; position: number; unit_id: number; unit_number: number; unit_title: string; clo_id: number; clo_code: string; clo_description: string; step_type: string; label: string; status: string; rationale: string; rule: string; meta: Record<string, unknown> }
export type Path = { id: number; version_no: number; trigger: string; reason: string | null; summary: string; locked: boolean; lock_reason: string | null; covers_all_clos: boolean; progress: { done: number; total: number }; next_step_id: number | null; steps: Step[]; created_at: string }

/** Where each step type takes the learner (features register their targets). */
export const STEP_LINKS: Record<string, (courseId: string, s: Step) => string | null> = {}

/** One icon per kind of step, so the path can be scanned without reading every title. */
const STEP_ICON: Record<string, LucideIcon> = { remedial: Layers, learn: BookOpen, practice: ClipboardList, review: Zap, reteach: RefreshCw }

/** The learning path as a timeline grouped by unit. Each step: icon (done = check), title, CLO, status, and a
 *  "Why this step?" explanation that opens on request (open for the next step, so the reason is never hidden).
 *  `quietNext`: the page already has a primary "next step" button, so the step's own Start button is secondary
 *  (one primary per section). `limit` shows only the next few unfinished steps (dashboard preview). */
export function PathSteps({ path, courseId, staff, quietNext, limit, skipNext }: { path: Path; courseId: string; staff?: boolean; quietNext?: boolean; limit?: number; skipNext?: boolean }) {
  let shown = path.steps
  if (limit) {
    const from = Math.max(0, path.steps.findIndex((s) => s.id === path.next_step_id))
    shown = path.steps.slice(from).filter((s) => s.status !== 'done' && !(skipNext && s.id === path.next_step_id)).slice(0, limit)
  }
  const units: { number: number; title: string; steps: Step[] }[] = []
  for (const s of shown) {
    const last = units[units.length - 1]
    if (last && last.number === s.unit_number) last.steps.push(s)
    else units.push({ number: s.unit_number, title: s.unit_title, steps: [s] })
  }
  const unitTotals = (n: number) => {
    const all = path.steps.filter((s) => s.unit_number === n)
    return `${all.filter((s) => s.status === 'done').length} of ${all.length} done`
  }
  return (
    <div className="steps">
      {units.map((u) => (
        <section key={`${u.number}-${u.steps[0].id}`} aria-labelledby={`unit-${u.number}-${u.steps[0].id}`}>
          <div className="tl-unit">
            <h3 id={`unit-${u.number}-${u.steps[0].id}`}><span>Unit {u.number}</span>{u.title}</h3>
            <span className="count">{unitTotals(u.number)}</span>
          </div>
          <ol className="tl">
            {u.steps.map((s) => {
              // step by step: only finished steps and the current one open; later steps are locked
              const link = !staff && (s.status !== 'pending' || s.id === path.next_step_id) && STEP_LINKS[s.step_type]?.(courseId, s)
              const isNext = s.id === path.next_step_id
              const done = s.status === 'done'
              const Icon = done ? Check : STEP_ICON[s.step_type] ?? Route
              return (
                <li key={s.id} className={`tl-step${done ? ' done' : ''}${isNext ? ' next' : ''}`} aria-current={isNext ? 'step' : undefined}>
                  <span className="tl-icon" aria-hidden="true"><Icon className="icon" /></span>
                  <div className="min-w-0">
                    <div className="tl-title">
                      <span className="pos">{s.position}.</span>
                      <span>{s.label}</span>
                      <span className="chip">{s.clo_code}</span>
                      {isNext ? <Badge value="up next" tone="ok" /> : !done && s.status !== 'pending' && <Badge value={s.status} />}
                      {done && <Badge value="done" />}
                    </div>
                    <p className="tl-desc">{s.clo_description}</p>
                    <details className="tl-why" open={isNext || undefined}>
                      <summary>Why this step?</summary>
                      <p>{s.rationale}</p>
                    </details>
                  </div>
                  {link && s.status === 'pending' && (
                    <Link className={`btn${isNext && !quietNext ? '' : ' secondary'} small tl-action`} to={link}>{isNext ? 'Start' : 'Open'}<span className="sr-only"> {s.label} for {s.clo_code}</span></Link>
                  )}
                </li>
              )
            })}
          </ol>
        </section>
      ))}
    </div>
  )
}

/** Path summary: progress ring, plain-language summary, and the unit-by-unit chart. */
export function PathOverview({ path }: { path: Path }) {
  const pct = (100 * path.progress.done) / Math.max(1, path.progress.total)
  return (
    <div className="card">
      <div className="progress-card">
        <Ring value={pct} label="Learning path progress" caption={`${path.progress.done} of ${path.progress.total} steps`} done={path.progress.done === path.progress.total} />
        <div className="facts">
          <p>{path.summary}</p>
          <p className="small muted">
            Version {path.version_no}
            {path.locked && <> · <Lock className="icon-inline" aria-hidden="true" /> fixed by your instructor{path.lock_reason ? `: ${path.lock_reason}` : ''}</>}
          </p>
          {path.reason && <p className="small"><strong>Latest change:</strong> {path.reason}</p>}
        </div>
      </div>
      <hr className="divider" />
      <PathChart steps={path.steps} nextId={path.next_step_id} />
    </div>
  )
}

export default function PathPage() {
  const { courseId = '' } = useParams()
  const p = useLoad(() => api<Path | null>(`/paths/me/${courseId}`), [courseId])
  const act = useAction()
  const crumbs = [{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: 'Course' }, { label: 'My learning path' }]
  if (p.loading && !p.data) return <><Crumbs items={crumbs} /><h1>My learning path</h1><Loading variant="table" /></>
  const next = p.data?.steps.find((s) => s.id === p.data?.next_step_id)
  const nextLink = next && STEP_LINKS[next.step_type]?.(courseId, next)
  return (
    <>
      <Crumbs items={crumbs} />
      <div className="page-header">
        <div>
          <h1>My learning path</h1>
          <p className="page-sub">Your personal route through the course, step by step.</p>
        </div>
        {nextLink && <Link className="btn" to={nextLink}>Continue: {next.label}<ChevronRight aria-hidden="true" /></Link>}
      </div>
      {p.error ? <Alert onRetry={p.reload}>{p.error}</Alert> : <Alert>{act.error}</Alert>}
      {!p.data ? (!p.error && (
        <EmptyState icon={Compass} title="No learning path yet" action={
          <div className="row">
            <Link className="btn" to={`/learn/${courseId}/diagnostic`}>Take the diagnostic</Link>
            <button className="secondary" aria-busy={act.busy || undefined} disabled={act.busy} onClick={async () => { if (await act.run(() => api(`/paths/me/${courseId}/generate`, { method: 'POST' }))) p.reload() }}>Start without the diagnostic</button>
          </div>
        }>
          The diagnostic gives you a personalised path; you can also start with the full course path.
        </EmptyState>
      )) : (
        <>
          <PathOverview path={p.data} />
          <div className="section-head"><h2><Route className="icon" aria-hidden="true" />All steps</h2></div>
          <PathSteps path={p.data} courseId={courseId} quietNext={!!nextLink} />
          <PathDecisions url={`/paths/me/${courseId}/decisions`} />
        </>
      )}
    </>
  )
}

type Decision = { id: number; at: string; clo_code: string; rule: string; decision: string; message: string; inputs: { mastery_pct: number; threshold: number; consecutive_failures: number } }

/** US-4.5 - why the path changed after each assessment. */
export function PathDecisions({ url }: { url: string }) {
  const d = useLoad(() => api<Decision[]>(url), [url])
  if (!d.data?.length) return null
  return (
    <details className="card mt-6">
      <summary><strong>How my path has adapted ({d.data.length})</strong></summary>
      <ul className="small">
        {d.data.map((x) => (
          <li key={x.id} className="mt-2">
            <Badge value={x.decision} /> <strong>{x.clo_code}</strong> — {x.message}{' '}
            <span className="muted">(mastery {x.inputs.mastery_pct}% vs {x.inputs.threshold}%, rule {x.rule}, {fmtDate(x.at)})</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
