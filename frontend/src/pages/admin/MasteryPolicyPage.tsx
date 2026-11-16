import { useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { Alert, Loading, useAction, useLoad } from '../../components/ui'

type Policy = { rule_version: string; window: number; summary: string; evidence_sources: string[]; criterion_version: string; amendments: string }

export default function MasteryPolicyPage() {
  const { user } = useAuth()
  const pol = useLoad(() => api<Policy>('/mastery/policy'))
  const courses = useLoad(() => api<{ id: number; code: string; title: string; status: string }[]>('/outcomes/courses'))
  const [form, setForm] = useState({ course_id: '', mode: 'reproduce', reason: '' })
  const act = useAction()
  const [result, setResult] = useState<{ records: number; changed: number } | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const r = await act.run(() => api<{ records: number; changed: number }>('/mastery/recalculate', { body: { ...form, course_id: Number(form.course_id) } }), 'Recalculation complete and logged')
    if (r) setResult(r)
  }

  return (
    <>
      <h1>Mastery rules</h1>
      {pol.loading ? <Loading variant="table" /> : pol.data && (
        <div className="card">
          <p><strong>Rule {pol.data.rule_version}</strong></p>
          <p>{pol.data.summary}</p>
          <ul>
            <li><strong>Evidence:</strong> scored attempts from {pol.data.evidence_sources.join(' and ')} activities using approved items only. Diagnostic results set readiness, not mastery.</li>
            <li><strong>Attempts counted:</strong> the latest {pol.data.window} per CLO.</li>
            <li><strong>Criteria version:</strong> {pol.data.criterion_version}</li>
            <li><strong>Evidence versioning:</strong> {pol.data.amendments}</li>
          </ul>
        </div>
      )}
      {user?.role === 'admin' && (
        <form className="card" onSubmit={submit}>
          <h2 className="mt-0">Recalculate a course</h2>
          <div className="grid">
            <div className="field"><label htmlFor="rc">Course</label>
              <select id="rc" required value={form.course_id} onChange={(e) => setForm({ ...form, course_id: e.target.value })}>
                <option value="">Choose…</option>{courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
              </select></div>
            <div className="field"><label htmlFor="rm">Mode</label>
              <select id="rm" value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
                <option value="reproduce">Reproduce (criteria in force at each attempt)</option>
                <option value="current_criteria">Apply current criteria</option>
              </select></div>
            <div className="field"><label htmlFor="rr">Reason</label><input id="rr" required value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></div>
          </div>
          <button disabled={act.busy}>Recalculate</button>
          <Alert>{act.error}</Alert>
          <Alert kind="success">{act.message}{result && ` — ${result.records} student/CLO records checked, ${result.changed} changed.`}</Alert>
        </form>
      )}
    </>
  )
}
