import { ArrowDown, ArrowUp, Compass, Lock, LockOpen, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { askConfirm } from '../../components/dialogApi'
import { Alert, Badge, EmptyState, fmtDate, useAction, useLoad } from '../../components/ui'
import type { Path, Step } from '../student/PathPage'
import { STUDENT_PANELS } from './ConsolePages'

type Ver = { id: number; version_no: number; status: string; trigger: string; reason: string | null; locked: boolean; created_at: string; restored_from: number | null; steps: number }
type Draft = { step_id?: number; clo_id: number; clo_code: string; step_type: string; status: string; label: string }

const TYPES = ['remedial', 'learn', 'practice', 'review', 'reteach']

function PathControlPanel({ courseId, studentId, detail, reload }: { courseId: string; studentId: string; detail: { path: Path | null; mastery: { clo_id: number; clo_code: string }[] }; reload: () => void }) {
  const base = `/paths/course/${courseId}/student/${studentId}`
  const vers = useLoad(() => api<Ver[]>(`${base}/versions`), [base])
  const act = useAction()
  const [reason, setReason] = useState('')
  const [draft, setDraft] = useState<Draft[] | null>(null)
  const [add, setAdd] = useState({ clo_id: '', step_type: 'practice' })
  const path = detail.path
  useEffect(() => setDraft(null), [path?.id])
  if (!path) return <EmptyState icon={Compass} title="No learning path yet">This learner gets a path after the diagnostic or when they start one.</EmptyState>

  async function post(url: string, body: object, msg: string, action: string, effect: string) {
    if (!(await askConfirm({ title: `${action}?`, message: `${effect} Your reason ("${reason.trim()}") is saved in the learner's path history.`, confirmLabel: action }))) return
    if (await act.run(() => api(url, { method: url.endsWith('/steps') ? 'PUT' : 'POST', body }), msg)) {
      setReason('')
      setDraft(null)
      vers.reload()
      reload()
    }
  }
  const move = (i: number, d: number) => {
    const x = [...draft!]
    const j = i + d
    if (j < 0 || j >= x.length) return
    ;[x[i], x[j]] = [x[j], x[i]]
    setDraft(x)
  }
  const reasonOk = reason.trim().length >= 10

  return (
    <>
      <p className="small">Path v{path.version_no} · {path.locked ? <Badge value="locked" /> : <Badge value="automatic" tone="ok" />}{path.lock_reason && ` — ${path.lock_reason}`}</p>
      <div className="field"><label htmlFor="pr">Reason for the change (required)</label><input id="pr" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Agreed a catch-up plan with the student" aria-describedby="pr-hint" /><p id="pr-hint" className="field-hint">At least 10 characters. Needed for Override, Lock, Rebuild and Restore.</p></div>
      <Alert>{act.error}</Alert>
      <div className="row">
        {path.locked
          ? <button className="secondary" disabled={!reasonOk} onClick={() => post(`${base}/unlock`, { reason }, 'Path unlocked — automatic adaptation resumes', 'Unlock path', 'Automatic adaptation will resume and may change the learner’s next steps.')}><LockOpen aria-hidden="true" />Unlock path</button>
          : <button className="secondary" disabled={!reasonOk} onClick={() => post(`${base}/lock`, { reason }, 'Path locked — automatic changes are paused', 'Lock path', 'Automatic changes to this learner’s path will pause until you unlock it.')}><Lock aria-hidden="true" />Lock path</button>}
        <button className="secondary" disabled={!reasonOk} onClick={() => post(`${base}/regenerate`, { reason }, 'Path rebuilt from the rules', 'Rebuild path', 'A new path version will be built from the current rules and mastery. The current version stays in the history.')}>Rebuild from rules</button>
        {!draft && <button onClick={() => setDraft(path.steps.map((s: Step) => ({ step_id: s.id, clo_id: s.clo_id, clo_code: s.clo_code, step_type: s.step_type, status: s.status, label: s.label })))}>Override path</button>}
      </div>

      {draft && (
        <div className="card mt-3">
          <h3 className="mt-0">Override path steps</h3>
          <ol className="small">
            {draft.map((s, i) => (
              <li key={`${s.step_id ?? 'new'}-${i}`} className="row mb-2">
                <span className="step-edit-label"><strong>{s.clo_code}</strong> {s.label} <Badge value={s.status} /></span>
                <button className="secondary small" aria-label={`Move step ${i + 1} up`} title="Move up" onClick={() => move(i, -1)}><ArrowUp aria-hidden="true" /></button>
                <button className="secondary small" aria-label={`Move step ${i + 1} down`} title="Move down" onClick={() => move(i, 1)}><ArrowDown aria-hidden="true" /></button>
                <button className="secondary small" aria-label={`Remove step ${i + 1}`} title="Remove step" onClick={() => setDraft(draft.filter((_, k) => k !== i))}><X aria-hidden="true" /></button>
              </li>
            ))}
          </ol>
          <div className="table-tools">
            <label className="inline-field"><span className="inline-field-label">Outcome</span><select value={add.clo_id} onChange={(e) => setAdd({ ...add, clo_id: e.target.value })}><option value="">CLO…</option>{detail.mastery.map((m) => <option key={m.clo_id} value={m.clo_id}>{m.clo_code}</option>)}</select></label>
            <label className="inline-field"><span className="inline-field-label">Step type</span><select value={add.step_type} onChange={(e) => setAdd({ ...add, step_type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
            <button className="secondary" disabled={!add.clo_id} onClick={() => {
              const m = detail.mastery.find((x) => x.clo_id === Number(add.clo_id))!
              setDraft([...draft, { clo_id: m.clo_id, clo_code: m.clo_code, step_type: add.step_type, status: 'pending', label: add.step_type }])
            }}>Add step</button>
          </div>
          <div className="card-actions">
            <button className="secondary" onClick={() => setDraft(null)}>Cancel</button>
            <button disabled={!reasonOk} onClick={() => post(`${base}/steps`, { reason, steps: draft.map((s) => (s.step_id ? { step_id: s.step_id } : { clo_id: s.clo_id, step_type: s.step_type })) }, 'Override saved as a new version', 'Save override', 'The learner’s path will change to these steps as a new version.')}>Save override</button>
          </div>
        </div>
      )}

      <h3>Version history</h3>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scroll sideways if it does not fit)"><table>
        <thead><tr><th>Version</th><th>Change</th><th>When</th><th><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{vers.data?.map((v) => (
          <tr key={v.id}>
            <td>v{v.version_no} <Badge value={v.status} /></td>
            <td className="small"><Badge value={v.trigger} /> {v.reason ?? ''}{v.restored_from && ` (restored from v${v.restored_from})`}</td>
            <td className="small">{fmtDate(v.created_at)}</td>
            <td>{v.status !== 'active' && <button className="secondary small" disabled={!reasonOk} onClick={() => post(`${base}/restore/${v.version_no}`, { reason }, `Version ${v.version_no} restored`, `Restore version ${v.version_no}`, `Version ${v.version_no} becomes the learner’s active path; the current version stays in the history.`)}>Restore version<span className="sr-only"> {v.version_no}</span></button>}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </>
  )
}

STUDENT_PANELS.push({ title: 'Learning path control', Component: PathControlPanel })
