import { Map as MapIcon, Upload } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { Alert, Badge, EmptyState, Loading, useAction, useLoad } from '../../components/ui'

type RoadmapRow = { id: number; semester: number; name: string; credits: number | null; credits_text: string | null; course_code: string | null; match_score: number | null; matched_by: string }
export type RoadmapData = {
  version_id: number; filename: string | null; engine?: string
  entries: RoadmapRow[]; courses: { code: string; title: string }[]
  summary: { entries: number; semesters: number; matched: number; roadmap_only: number; courses_without_semester: string[] }
}

const BY_LABEL: Record<string, { value: string; tone: string }> = {
  code: { value: 'Matched by code', tone: 'ok' }, exact: { value: 'Same name', tone: 'ok' },
  similar: { value: 'Similar name — check', tone: 'warn' }, ai: { value: 'Matched by AI — check', tone: 'warn' },
  manual: { value: 'Set by you', tone: 'ok' }, none: { value: 'Roadmap only (no CLOs)', tone: '' },
}

/** Semester roadmap of a curriculum version: upload, then check / correct which curriculum course each row is. */
export default function RoadmapReview({ versionId, initial }: { versionId: number; initial?: RoadmapData | null }) {
  const r = useLoad(() => (initial ? Promise.resolve(initial) : api<RoadmapData>(`/curriculum/versions/${versionId}/roadmap`)), [versionId, initial])
  const [edits, setEdits] = useState<Record<number, string>>({})
  const [file, setFile] = useState<File | null>(null)
  const act = useAction()
  const data = r.data

  async function upload() {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    const res = await act.run(() => api<RoadmapData>(`/curriculum/versions/${versionId}/roadmap`, { form }), 'Roadmap read and matched')
    if (res) { r.setData(res); setEdits({}); setFile(null) }
  }
  async function save() {
    const entries = Object.entries(edits).map(([id, code]) => ({ id: Number(id), course_code: code || null }))
    const res = await act.run(() => api<RoadmapData>(`/curriculum/versions/${versionId}/roadmap`, { method: 'PUT', body: { entries } }), 'Roadmap matches saved')
    if (res) { r.setData(res); setEdits({}) }
  }

  const uploader = (
    <div className="table-tools">
      <label className="inline-field"><span className="inline-field-label">Roadmap file (PDF, Excel, Word or text with Semester 1, 2… columns)</span>
        <input type="file" accept=".pdf,.xlsx,.docx,.md,.txt,.html" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
      <button className="secondary" disabled={!file || act.busy} aria-busy={act.busy || undefined} onClick={upload}><Upload aria-hidden="true" />{data?.entries.length ? 'Replace roadmap' : 'Read roadmap'}</button>
    </div>
  )

  if (r.loading && !data) return <Loading variant="table" />
  if (r.error) return <Alert onRetry={r.reload}>{r.error}</Alert>
  const semesters = [...new Set((data?.entries ?? []).map((e) => e.semester))].sort((a, b) => a - b)
  const usedCodes = new Set((data?.entries ?? []).map((e) => (e.id in edits ? edits[e.id] : e.course_code)).filter(Boolean))
  return (
    <section aria-labelledby={`roadmap-${versionId}`} className="mt-6">
      <h3 id={`roadmap-${versionId}`}>Semester roadmap</h3>
      <Alert>{act.error}</Alert>
      {!data?.entries.length ? (
        <>
          <EmptyState icon={MapIcon} title="No roadmap yet">Add the programme's semester plan so students can only enrol in courses of their current semester.</EmptyState>
          {uploader}
        </>
      ) : (
        <>
          <p className="card-desc">
            {data.filename && <><strong>{data.filename}</strong> · </>}{data.summary.entries} courses in {data.summary.semesters} semesters ·
            {' '}{data.summary.matched} matched to curriculum courses · {data.summary.roadmap_only} roadmap-only (no CLOs: shown to students, not limited)
          </p>
          {data.summary.courses_without_semester.length > 0 && (
            <Alert kind="warning" title="Curriculum courses not in the roadmap">
              {data.summary.courses_without_semester.join(', ')} — students can enrol in these at any time. Pick them for the right roadmap row below if they belong to a semester.
            </Alert>
          )}
          {semesters.map((s) => (
            <div key={s} className="table-wrap" tabIndex={0} role="region" aria-label={`Semester ${s}`}>
              <table>
                <caption className="sr-only">Semester {s}</caption>
                <thead><tr><th>Semester {s}</th><th className="num">Credits</th><th>Curriculum course</th><th>How it was matched</th></tr></thead>
                <tbody>{data.entries.filter((e) => e.semester === s).map((e) => {
                  const value = e.id in edits ? edits[e.id] : e.course_code ?? ''
                  const by = e.id in edits ? (edits[e.id] ? 'manual' : 'none') : e.matched_by
                  return (
                    <tr key={e.id}>
                      <td>{e.name}</td>
                      <td className="num">{e.credits_text ?? '—'}</td>
                      <td>
                        <select aria-label={`Curriculum course for ${e.name}`} value={value} onChange={(ev) => setEdits({ ...edits, [e.id]: ev.target.value })}>
                          <option value="">Not in the CLO file (roadmap only)</option>
                          {data.courses.map((c) => <option key={c.code} value={c.code} disabled={c.code !== value && usedCodes.has(c.code)}>{c.code} — {c.title}</option>)}
                        </select>
                      </td>
                      <td><Badge value={BY_LABEL[by]?.value ?? by} tone={BY_LABEL[by]?.tone ?? ''} /></td>
                    </tr>
                  )
                })}</tbody>
              </table>
            </div>
          ))}
          <div className="card-actions">
            <button className="secondary" disabled={!Object.keys(edits).length} onClick={() => setEdits({})}>Discard changes</button>
            <button disabled={!Object.keys(edits).length || act.busy} aria-busy={act.busy || undefined} onClick={save}>Save roadmap matches</button>
          </div>
          <details className="mt-3"><summary className="small">Replace with another roadmap file</summary>{uploader}</details>
        </>
      )}
    </section>
  )
}
