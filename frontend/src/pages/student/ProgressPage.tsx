import { AlertTriangle, BarChart3, CheckCircle2, ChevronRight, Download, Flag, ListChecks, Target } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api, download } from '../../api/client'
import { MasteryChart, StatTile } from '../../components/charts'
import { Alert, Badge, Crumbs, EmptyState, Loading, useAction, useLoad } from '../../components/ui'
import { STEP_LINKS, type Step } from './PathPage'
import { PastQuizzes } from './QuizReviewPage'

type Row = { clo_id: number; clo_code: string; description: string; threshold: number; mastery_pct: number | null; attained: boolean; status: string; attempts: number; readiness: string | null }
type Next = { step_id: number; clo_id: number; clo_code: string; unit_id: number; step_type: string; label: string; rationale: string }
export type Progress = {
  student: { name: string; student_number: string | null }; course: { id: number; code: string; title: string }
  summary: { attained: number; total: number; attained_pct: number; path_done: number; path_total: number }
  clos: Row[]; weak_areas: { clo_code: string; description: string; status: string; mastery_pct: number | null; gap: number | null }[]
  next_steps: Next[]; advice: string[]; policy: string
}

/** Per-CLO mastery (DESIGN.md §5): CLO code + short name + bar with the target marker + status badge. */
export function CloMasteryList({ rows, compact }: { rows: Row[]; compact?: boolean }) {
  return <div className="card"><MasteryChart rows={rows} compact={compact} /></div>
}

/** Weak areas with a "Practise now" action per outcome (students only). The advice lines repeat the same
 *  outcomes in words, so they are shown only when there is no list to show. */
export function WeakAreas({ p, courseId, staff }: { p: Progress; courseId: string; staff?: boolean }) {
  if (!p.weak_areas.length && !p.advice.length) {
    return <EmptyState icon={CheckCircle2} title="No weak areas">You are at or above the mastery target on every assessed outcome — well done.</EmptyState>
  }
  const idFor = (code: string) => p.clos.find((c) => c.clo_code === code)?.clo_id
  if (!p.weak_areas.length) return <div className="card"><ul className="small m-0">{p.advice.map((a, i) => <li key={i}>{a}</li>)}</ul></div>
  return (
    <ul className="clo-list card">
      {p.weak_areas.map((w) => {
        const cloId = idFor(w.clo_code)
        return (
          <li key={w.clo_code}>
            <div><strong>{w.clo_code}</strong> <span className="small muted">{w.description}</span></div>
            <div className="small">{w.mastery_pct === null ? 'Not assessed yet' : <><AlertTriangle className="icon-inline text-warning" aria-hidden="true" /> {w.mastery_pct}% mastery{w.gap ? ` — ${w.gap}% below target` : ''}</>}</div>
            <div>{!staff && cloId ? <Link className="btn secondary small" to={`/learn/${courseId}/quiz/${cloId}`}>Take the quiz now<span className="sr-only"> {w.clo_code}</span></Link> : <Badge value={w.status} />}</div>
          </li>
        )
      })}
    </ul>
  )
}

export function ProgressView({ p, courseId, staff }: { p: Progress; courseId: string; staff?: boolean }) {
  const pathPct = Math.round((100 * p.summary.path_done) / Math.max(1, p.summary.path_total))
  return (
    <>
      <div className="tiles">
        <StatTile icon={Target} tone="ok" label="Outcomes mastered" value={`${p.summary.attained} / ${p.summary.total}`} sub={`${Math.round(p.summary.attained_pct)}% of the course outcomes`} />
        <StatTile icon={Flag} label="Learning path" value={`${pathPct}%`} sub={`${p.summary.path_done} of ${p.summary.path_total} steps done`} />
        <StatTile icon={AlertTriangle} tone={p.weak_areas.length ? 'warn' : 'ok'} label="Weak areas" value={p.weak_areas.length} sub={p.weak_areas.length ? 'outcomes to practise' : 'nothing below target'} />
      </div>
      <div className="section-head"><h2><BarChart3 className="icon" aria-hidden="true" />Mastery by outcome</h2></div>
      {p.clos.length ? <CloMasteryList rows={p.clos} /> : <EmptyState title="No outcomes yet">This course has no learning outcomes published yet.</EmptyState>}
      <div className="section-head"><h2><AlertTriangle className="icon" aria-hidden="true" />Weak areas</h2></div>
      <WeakAreas p={p} courseId={courseId} staff={staff} />
      <div className="section-head"><h2><ListChecks className="icon" aria-hidden="true" />Next steps</h2></div>
      {p.next_steps.length === 0 ? <EmptyState icon={CheckCircle2} title="Learning path complete">There are no steps left on your path.</EmptyState> : (
        <ol className="tl">{p.next_steps.map((s, i) => {
          const link = !staff && STEP_LINKS[s.step_type]?.(courseId, { id: s.step_id, clo_id: s.clo_id, unit_id: s.unit_id } as Step)
          return (
            <li key={s.step_id} className={`tl-step${i === 0 ? ' next' : ''}`}>
              <span className="tl-icon" aria-hidden="true"><span className="small">{i + 1}</span></span>
              <div className="min-w-0">
                <div className="tl-title"><span>{s.label}</span><span className="chip">{s.clo_code}</span></div>
                <p className="tl-desc">{s.rationale}</p>
              </div>
              {link && <Link className={`btn small tl-action${i === 0 ? '' : ' secondary'}`} to={link}>Start<span className="sr-only"> {s.label} for {s.clo_code}</span><ChevronRight aria-hidden="true" /></Link>}
            </li>
          )
        })}</ol>
      )}
      {!staff && <div className="mt-6"><PastQuizzes courseId={courseId} /></div>}
      <p className="small muted mt-6">{p.policy}</p>
    </>
  )
}

export default function ProgressPage() {
  const { courseId = '' } = useParams()
  const p = useLoad(() => api<Progress>(`/progress/me/${courseId}`), [courseId])
  const act = useAction()
  const crumbs = [{ to: '/learn', label: 'My learning' }, { to: `/learn/${courseId}`, label: p.data?.course.code ?? 'Course' }, { label: 'My progress' }]
  if (p.loading && !p.data) return <><Crumbs items={crumbs} /><h1>My progress</h1><Loading variant="cards" /></>
  if (!p.data) return <><Crumbs items={crumbs} /><h1>My progress</h1><Alert onRetry={p.reload}>{p.error}</Alert></>
  const file = `progress-${p.data.course.code}`
  return (
    <>
      <Crumbs items={crumbs} />
      <div className="page-header">
        <div>
          <h1>My progress</h1>
          <p className="page-sub">{p.data.course.code} — {p.data.course.title}</p>
        </div>
        <div className="page-actions">
          <button className="secondary" aria-busy={act.busy || undefined} onClick={() => act.run(() => download(`/progress/me/${courseId}/download?format=pdf`, `${file}.pdf`))}><Download aria-hidden="true" />Download progress (PDF)</button>
          <button className="ghost" onClick={() => act.run(() => download(`/progress/me/${courseId}/download?format=csv`, `${file}.csv`))}>Download CSV</button>
        </div>
      </div>
      <Alert>{act.error}</Alert>
      <ProgressView p={p.data} courseId={courseId} />
    </>
  )
}
