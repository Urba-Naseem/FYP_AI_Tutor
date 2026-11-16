/**
 * Turns API data the app already returns into the shapes the new visuals need (no new endpoints, nothing invented):
 *  - learning path (GET /paths/me/{course}) -> PathMap nodes
 *  - mastery history (GET /mastery/me/{course}) + diagnostic results -> mastery-over-time series
 */
import type { CloSeries, MasteryPoint } from '../../components/kit/Charts'
import type { PathNode, PathNodeKind } from '../../components/kit/PathMap'
import { STEP_LINKS, type Path, type Step } from './PathPage'

const KIND: Record<string, PathNodeKind> = { remedial: 'remedial', learn: 'lesson', practice: 'quiz', review: 'quiz', reteach: 'reteach' }

export type MasteryClo = {
  clo_id: number; clo_code: string; description: string
  current: { mastery_pct: number; attained: boolean; threshold: number } | null
  evidence: { source: string; correct: number; total: number; score_pct: number; recorded_at: string; status: string }[]
}
export type DiagResult = { clo_id: number; clo_code: string; score_pct: number; level: string }

const shortDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })

/** Path steps -> map nodes. A reteach step branches off the practice step of the same CLO just before it; the
 *  diagnostic (when taken) is shown first. Scores come from current mastery for finished practice steps. */
export function pathNodes(path: Path, courseId: string, threshold: number, mastery: Map<number, { pct: number; attained: boolean }>, diagnosticDone: boolean): PathNode[] {
  const nodes: PathNode[] = []
  const first = path.steps[0]
  if (diagnosticDone && first) {
    nodes.push({ id: 'diag', unit: first.unit_number, unitTitle: first.unit_title, kind: 'diagnostic', clo: 'All CLOs', title: 'Readiness check', status: 'done', threshold,
      reason: 'Everyone starts with the same diagnostic test; your path below was built from your results.' })
  }
  let lastPractice: Step | null = null
  for (const s of path.steps) {
    const kind = KIND[s.step_type] ?? 'lesson'
    const m = mastery.get(s.clo_id)
    const done = s.status === 'done'
    const isBranch = kind === 'reteach' && lastPractice && lastPractice.clo_id === s.clo_id
    nodes.push({
      id: s.id, unit: s.unit_number, unitTitle: s.unit_title, kind, clo: s.clo_code, title: s.label,
      status: done ? 'done' : s.id === path.next_step_id ? 'current' : s.status === 'skipped' ? 'skipped' : 'upcoming',
      score: done && (kind === 'quiz' || kind === 'reteach') && m ? m.pct : null, attained: m?.attained ?? null, threshold,
      reason: s.rationale, branchOf: isBranch ? lastPractice!.id : undefined,
      // step by step: later steps are locked (no link) until the earlier ones are done
      loopLabel: isBranch ? `Reteach · ${s.clo_code}` : undefined, href: (done || s.id === path.next_step_id) ? STEP_LINKS[s.step_type]?.(courseId, s) ?? undefined : undefined,
    })
    if (kind === 'quiz') lastPractice = s
  }
  return nodes
}

/** Diagnostic score first, then every counted quiz / reteach attempt in time order. */
/** Same rule as the server (backend/app/services/mastery.py, best-of-last-3/v1): mastery after an attempt is the
 *  best score among the 3 most recent active quiz/reteach attempts. The diagnostic sets readiness only. */
export const MASTERY_WINDOW = 3

export function masterySeries(clos: MasteryClo[], diag: DiagResult[], threshold: number): CloSeries[] {
  return clos.map((c) => {
    const points: MasteryPoint[] = []
    const d = diag.find((x) => x.clo_id === c.clo_id)
    if (d) points.push({ label: 'Diagnostic', date: '', activity: 'Diagnostic', score: null, readiness: Math.round(d.score_pct) })
    const ev = c.evidence.filter((e) => e.status === 'active').sort((a, b) => a.recorded_at.localeCompare(b.recorded_at))
    let quizzes = 0, reteaches = 0
    ev.forEach((e, i) => {
      const reteach = e.source === 'reteach'
      const n = reteach ? ++reteaches : ++quizzes
      const recent = ev.slice(Math.max(0, i + 1 - MASTERY_WINDOW), i + 1)
      points.push({
        label: `${reteach ? 'Reteach' : 'Quiz'} ${n}`, date: shortDate(e.recorded_at), activity: reteach ? 'Reteach quiz' : 'Formative quiz',
        score: Math.round(Math.max(...recent.map((r) => r.score_pct))), attemptScore: Math.round(e.score_pct), detail: `${e.correct} / ${e.total} correct`,
      })
    })
    return { clo: c.clo_code, description: c.description, threshold: c.current?.threshold ?? threshold, points }
  })
}
