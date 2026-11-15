import { useState } from 'react'
import { api } from '../../api/client'
import { Alert, Badge, fmtDate, useAction, useLoad } from '../../components/ui'

type R = { id: number; doc_title: string; version_no: number; category: string; message: string; excerpt: string | null; status: string; created_at: string }

/** Learner-reported problems in published material (US-5.1). */
export default function ContentReports({ courseId }: { courseId: string }) {
  const list = useLoad(() => api<R[]>(`/content-reports?course_id=${courseId}`), [courseId])
  const [res, setRes] = useState<Record<number, string>>({})
  const act = useAction()
  if (!list.data?.length) return null
  return (
    <div className="card">
      <h2 className="mt-0">Problems reported by students</h2>
      {list.data.map((r) => (
        <div key={r.id} className="divided">
          <div className="row"><Badge value={r.category} /><strong>{r.doc_title}</strong><span className="small muted">v{r.version_no} · {fmtDate(r.created_at)}</span></div>
          <p className="small">{r.message}{r.excerpt && <><br /><em>“{r.excerpt}”</em></>}</p>
          <div className="row">
            <label className="inline-field"><span className="inline-field-label">Resolution</span><input placeholder="What did you change?" value={res[r.id] ?? ''} onChange={(e) => setRes({ ...res, [r.id]: e.target.value })} className="max-w-sm" /></label>
            <button className="secondary" onClick={async () => { if (await act.run(() => api(`/content-reports/${r.id}/resolve`, { body: { resolution: res[r.id] ?? '' } }))) list.reload() }}>Mark resolved</button>
          </div>
        </div>
      ))}
      <Alert>{act.error}</Alert>
    </div>
  )
}
