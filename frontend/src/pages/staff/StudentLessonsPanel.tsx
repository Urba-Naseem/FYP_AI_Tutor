/** Learner page panel: the personal lessons the AI wrote for this student. The instructor can read one, edit it
 *  by hand, or have the AI rewrite it with a note ("use a cooking analogy; loops confuse her"). */
import { Eye, Pencil, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { askText } from '../../components/dialogApi'
import { Button, Drawer, StatusPill } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, fmtDate, useAction, useLoad } from '../../components/ui'
import { STUDENT_PANELS } from './ConsolePages'

type Row = { id: number; topic_id: number | null; topic_title: string | null; topic_number: number | null; clo_code: string | null; level_label: string; created_at: string; edited: boolean; instructor_note: string | null }
type Full = Row & { lecture_md: string; notes_md: string }

function StudentLessonsPanel({ courseId, studentId }: { courseId: string; studentId: string }) {
  const list = useLoad(() => api<Row[]>(`/topics/course/${courseId}/student/${studentId}/lessons`), [courseId, studentId])
  const [open, setOpen] = useState<Full | null>(null)
  const [editing, setEditing] = useState<{ lecture_md: string; notes_md: string } | null>(null)
  const act = useAction()
  const show = async (id: number, edit = false) => {
    const r = await act.run(() => api<Full>(`/topics/lessons/${id}`))
    if (r) { setOpen(r); setEditing(edit ? { lecture_md: r.lecture_md, notes_md: r.notes_md } : null) }
  }
  const rewrite = async (r: Row) => {
    const note = await askText({ title: 'Rewrite this lesson with the AI', message: `Tell the AI what to change for this student (e.g. "simpler words, use a cooking analogy, more worked examples").`, inputLabel: 'Your note to the AI', confirmLabel: 'Rewrite lesson' })
    if (!note) return
    if (await act.run(() => api(`/topics/lessons/${r.id}/rewrite`, { body: { note } }), 'Lesson rewritten — the student sees the new version')) list.reload()
  }
  const saveEdit = async () => {
    if (!open || !editing) return
    if (await act.run(() => api(`/topics/lessons/${open.id}`, { method: 'PUT', body: editing }), 'Lesson saved — the student sees your version')) { setOpen(null); setEditing(null); list.reload() }
  }
  if (list.loading && !list.data) return <p className="small muted">Loading lessons…</p>
  if (list.error) return <Alert onRetry={list.reload}>{list.error}</Alert>
  return (
    <>
      <p className="small muted">Lessons the AI wrote for this learner (each pitched to their level). If they are struggling, edit a lesson or have it rewritten with your guidance.</p>
      <Alert>{act.error}</Alert>
      {!list.data?.length ? <p className="kempty">No lessons yet — they are written when the learner opens a topic.</p> : (
        <ol className="topic-list">
          {list.data.map((r) => (
            <li key={r.id}>
              <span className="topic-num" aria-hidden="true">{r.topic_number ?? '–'}</span>
              <div className="min-w-0">
                <strong>{r.topic_title ?? `${r.clo_code} lesson`}</strong> {r.edited && <StatusPill status="review">Changed by instructor</StatusPill>}
                <p className="topic-meta">{r.clo_code} · {r.level_label} level · {fmtDate(r.created_at)}{r.instructor_note ? ` · note: “${r.instructor_note}”` : ''}</p>
              </div>
              <div className="lesson-row-actions">
                <Button variant="ghost" size="sm" icon={<Eye />} onClick={() => show(r.id)} aria-label={`Read lesson ${r.topic_title ?? r.clo_code}`}>Read</Button>
                <Button variant="ghost" size="sm" icon={<Pencil />} onClick={() => show(r.id, true)} aria-label={`Edit lesson ${r.topic_title ?? r.clo_code}`}>Edit</Button>
                <Button variant="ghost" size="sm" icon={<Sparkles />} loading={act.busy} onClick={() => rewrite(r)} aria-label={`Rewrite lesson ${r.topic_title ?? r.clo_code} with the AI`}>Rewrite</Button>
              </div>
            </li>
          ))}
        </ol>
      )}
      <Drawer open={!!open} onClose={() => { setOpen(null); setEditing(null) }} title={open ? (open.topic_title ?? `${open.clo_code} lesson`) : 'Lesson'}
        footer={editing ? <><Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button><Button loading={act.busy} onClick={saveEdit}>Save lesson</Button></> : undefined}>
        {open && (editing ? (
          <>
            <div className="kfield"><label className="kfield-label" htmlFor="lesson-md">Lesson (Markdown)</label>
              <textarea id="lesson-md" rows={18} value={editing.lecture_md} onChange={(e) => setEditing({ ...editing, lecture_md: e.target.value })} /></div>
            <div className="kfield"><label className="kfield-label" htmlFor="notes-md">Revision notes</label>
              <textarea id="notes-md" rows={6} value={editing.notes_md} onChange={(e) => setEditing({ ...editing, notes_md: e.target.value })} /></div>
            <Alert>{act.error}</Alert>
          </>
        ) : (
          <><Markdown>{open.lecture_md}</Markdown>{open.notes_md && <><h3>Revision notes</h3><Markdown>{open.notes_md}</Markdown></>}</>
        ))}
      </Drawer>
    </>
  )
}

STUDENT_PANELS.push({ title: 'Personal lessons', Component: StudentLessonsPanel })
