/** Staff: the contact details students see on their course page ("Your instructor"). */
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import { Alert, useAction, useLoad } from '../components/ui'

type Me = { office_hours: string | null; office_location: string | null; about: string | null }

export function StaffProfile() {
  const me = useLoad(() => api<Me>('/auth/me'))
  const [f, setF] = useState<Me | null>(null)
  const act = useAction()
  const v = f ?? me.data ?? { office_hours: '', office_location: '', about: '' }
  async function save(e: FormEvent) {
    e.preventDefault()
    await act.run(() => api('/auth/profile', { method: 'PATCH', body: v }), 'Profile saved — your students see it on their course page')
  }
  return (
    <form className="card max-w-md" onSubmit={save}>
      <h2>What your students see</h2>
      <p className="small muted">Shown on the course page of every student in your sections, with your name and institutional e-mail.</p>
      <div className="field"><label htmlFor="oh">Office hours</label>
        <input id="oh" aria-describedby="oh-hint" value={v.office_hours ?? ''} onChange={(e) => setF({ ...v, office_hours: e.target.value })} maxLength={200} />
        <p id="oh-hint" className="field-hint">For example: Mon and Wed 2:00–4:00 PM (PKT), or by appointment.</p></div>
      <div className="field"><label htmlFor="ol">Office</label>
        <input id="ol" value={v.office_location ?? ''} onChange={(e) => setF({ ...v, office_location: e.target.value })} maxLength={200} /></div>
      <div className="field"><label htmlFor="ab">A short introduction</label>
        <textarea id="ab" rows={3} value={v.about ?? ''} onChange={(e) => setF({ ...v, about: e.target.value })} maxLength={1500} /></div>
      <Alert>{act.error}</Alert>
      {act.message && <Alert kind="success">{act.message}</Alert>}
      <button disabled={act.busy}>Save profile</button>
    </form>
  )
}
