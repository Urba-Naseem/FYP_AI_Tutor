import { useState } from 'react'
import { api } from '../../api/client'
import { Alert, Badge, fmtDate, useAction, useLoad } from '../../components/ui'
import { STUDENT_PANELS } from './ConsolePages'

type Iv = { id: number; action: string; reason: string; at: string; instructor: string; details: Record<string, unknown> }
type Esc = { id: number; clo_code: string | null; trigger: string; summary: string; status: string; created_at: string; interventions: Iv[] }

const ACTIONS: Record<string, string> = {
  note: 'Add a note',
  meeting: 'Record a meeting / contact',
  reset_attempts: 'Reset reteach attempts (reopen)',
  override_advance: 'Let the learner move past this outcome',
  mastery_override: 'Override the mastery decision',
}

function InterventionPanel({ courseId, studentId, detail, reload }: { courseId: string; studentId: string; detail: { mastery: { clo_id: number; clo_code: string }[] }; reload: () => void }) {
  const esc = useLoad(() => api<Esc[]>(`/escalations?course_id=${courseId}&student_id=${studentId}`), [courseId, studentId])
  const ivs = useLoad(() => api<Iv[]>(`/interventions?course_id=${courseId}&student_id=${studentId}`), [courseId, studentId])
  const act = useAction()
  const [f, setF] = useState({ action: 'note', clo_id: '', reason: '', attained: 'true', escalation_id: '', resolve: false })
  const needsClo = ['reset_attempts', 'override_advance', 'mastery_override'].includes(f.action)

  async function submit() {
    const body = {
      student_id: Number(studentId), course_id: Number(courseId), action: f.action, reason: f.reason,
      clo_id: f.clo_id ? Number(f.clo_id) : null, escalation_id: f.escalation_id ? Number(f.escalation_id) : null,
      attained: f.action === 'mastery_override' ? f.attained === 'true' : null, resolve: f.resolve,
    }
    if (await act.run(() => api('/interventions', { body }), 'Intervention recorded')) {
      setF({ ...f, reason: '' })
      esc.reload(); ivs.reload(); reload()
    }
  }

  return (
    <>
      {esc.data?.filter((e) => e.status !== 'resolved').map((e) => (
        <Alert key={e.id} kind="error">
          <Badge value={e.status} /> <strong>Escalation{e.clo_code ? ` on ${e.clo_code}` : ''}</strong> ({e.trigger.replace(/_/g, ' ')}, {fmtDate(e.created_at)}): {e.summary}
          {e.status === 'open' && <button className="secondary small ml-2" onClick={async () => { if (await act.run(() => api(`/escalations/${e.id}/acknowledge`, { method: 'POST' }))) esc.reload() }}>Acknowledge</button>}
        </Alert>
      ))}
      <div className="grid">
        <div className="field"><label htmlFor="ia">Action</label>
          <select id="ia" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}>{Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div className="field"><label htmlFor="ic">Outcome {needsClo ? '(required)' : '(optional)'}</label>
          <select id="ic" value={f.clo_id} onChange={(e) => setF({ ...f, clo_id: e.target.value })}><option value="">—</option>{detail.mastery.map((m) => <option key={m.clo_id} value={m.clo_id}>{m.clo_code}</option>)}</select></div>
        {f.action === 'mastery_override' && (
          <div className="field"><label htmlFor="iat">Record as</label>
            <select id="iat" value={f.attained} onChange={(e) => setF({ ...f, attained: e.target.value })}><option value="true">Attained</option><option value="false">Not attained</option></select></div>
        )}
        <div className="field"><label htmlFor="ie">Linked escalation</label>
          <select id="ie" value={f.escalation_id} onChange={(e) => setF({ ...f, escalation_id: e.target.value })}>
            <option value="">—</option>{esc.data?.filter((e) => e.status !== 'resolved').map((e) => <option key={e.id} value={e.id}>#{e.id} {e.clo_code ?? ''} {e.trigger}</option>)}
          </select></div>
      </div>
      <div className="field"><label htmlFor="ir">Reason (required, recorded in the audit trail)</label><textarea id="ir" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></div>
      {f.escalation_id && <label className="inline-check"><input type="checkbox" checked={f.resolve} onChange={(e) => setF({ ...f, resolve: e.target.checked })} /> This resolves the escalation</label>}
      <Alert>{act.error}</Alert>
      <button disabled={act.busy || f.reason.trim().length < 10 || (needsClo && !f.clo_id)} onClick={submit}>Record intervention</button>
      {ivs.data && ivs.data.length > 0 && (
        <details className="mt-3">
          <summary className="small">Intervention history ({ivs.data.length})</summary>
          <ul className="small">{ivs.data.map((i) => <li key={i.id}>{fmtDate(i.at)} — <strong>{ACTIONS[i.action] ?? i.action}</strong> by {i.instructor}: {i.reason}</li>)}</ul>
        </details>
      )}
    </>
  )
}

STUDENT_PANELS.push({ title: 'Escalations & interventions', Component: InterventionPanel })
