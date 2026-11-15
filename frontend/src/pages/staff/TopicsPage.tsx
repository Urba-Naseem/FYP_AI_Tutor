/** Instructor: the course's topics (about 4 per credit hour). Topics are published straight away; here the instructor
 *  edits titles and summaries, changes which CLOs a topic serves, hides or adds topics, and re-plans a plain
 *  (fallback) plan with the AI while no student has a lesson for it yet. */
import { EyeOff, Pencil, Plus, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { Button, Card, Field, Modal, Skeleton, StatusPill } from '../../components/kit/Kit'
import { Alert, EmptyState, useAction, useLoad } from '../../components/ui'

type T = { id: number; number: number; title: string; summary: string; status: string; origin: string; unit_number: number | null; unit_title: string | null; clos: { id: number; code: string; description: string }[] }
type Data = { course: { id: number; code: string; title: string; credits: number | null }; target: number; can_replan: boolean; topics: T[] }
type Course = { course: { id: number; code: string; title: string } }
type Form = { id?: number; title: string; summary: string; clo_ids: number[] }

export default function TopicsPage() {
  const ov = useLoad(() => api<Course[]>('/console/overview'))
  const [picked, setPicked] = useState<number | null>(null)
  const courses = (ov.data ?? []).map((x) => x.course)
  const cid = picked ?? courses[0]?.id ?? null
  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb">Content</p>
          <h1>Course topics</h1>
          <p className="phead-desc">The AI plans a semester of topics from the course outcomes. Students see them straight away; change anything here.</p>
        </div>
        {courses.length > 1 && (
          <div className="phead-actions">
            <label className="kselect"><span className="sr-only">Course</span>
              <select value={cid ?? ''} onChange={(e) => setPicked(Number(e.target.value))}>{courses.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.title}</option>)}</select>
            </label>
          </div>
        )}
      </div>
      {ov.loading && !ov.data ? <Skeleton lines={6} label="Loading your courses" /> : ov.error ? <Alert onRetry={ov.reload}>{ov.error}</Alert>
        : cid == null ? <EmptyState icon={Pencil} title="No courses yet">Courses you teach appear here once an administrator assigns you.</EmptyState>
          : <CourseTopics key={cid} courseId={cid} />}
    </>
  )
}

function CourseTopics({ courseId }: { courseId: number }) {
  const d = useLoad(() => api<Data>(`/topics/course/${courseId}`), [courseId])
  const act = useAction()
  const [form, setForm] = useState<Form | null>(null)
  if (d.loading && !d.data) return <Skeleton lines={8} label="Planning the course topics — the first time this can take a few seconds" />
  if (!d.data) return <Alert onRetry={d.reload}>{d.error}</Alert>
  const x = d.data
  const clos = [...new Map(x.topics.flatMap((t) => t.clos).map((c) => [c.id, c])).values()].sort((a, b) => a.code.localeCompare(b.code))
  const save = async () => {
    if (!form) return
    const body = { title: form.title, summary: form.summary, clo_ids: form.clo_ids }
    const ok = await act.run(() => (form.id ? api(`/topics/${form.id}`, { method: 'PATCH', body }) : api(`/topics/course/${courseId}`, { body })), form.id ? 'Topic saved' : 'Topic added')
    if (ok) { setForm(null); d.reload() }
  }
  const toggle = async (t: T) => {
    if (await act.run(() => api(`/topics/${t.id}`, { method: 'PATCH', body: { status: t.status === 'hidden' ? 'published' : 'hidden' } }), t.status === 'hidden' ? 'Topic shown to students' : 'Topic hidden from students')) d.reload()
  }
  return (
    <>
      <Card title={`${x.course.code} · ${x.topics.length} topics`} subtitle={`Planned for ${x.course.credits ? `${x.course.credits} credit hours` : 'a standard semester'} (target ${x.target})${x.topics.some((t) => t.origin === 'fallback') ? ' · plain plan made without the AI' : ''}`}
        action={<div className="row">
          {x.can_replan && <Button variant="secondary" size="sm" icon={<Sparkles />} loading={act.busy} onClick={async () => { if (await act.run(() => api(`/topics/course/${courseId}/replan`, { method: 'POST' }), 'Topics re-planned with the AI')) d.reload() }}>Re-plan with AI</Button>}
          <Button size="sm" icon={<Plus />} onClick={() => setForm({ title: '', summary: '', clo_ids: clos[0] ? [clos[0].id] : [] })}>Add topic</Button>
        </div>}>
        <Alert>{act.error}</Alert>
        <p className="small muted">New or changed topics reach a student's path when their path is next rebuilt (after a quiz decision or a path rebuild in the learner page). Lessons already written are kept.</p>
        <ol className="topic-list">
          {x.topics.map((t) => (
            <li key={t.id}>
              <span className="topic-num" aria-hidden="true">{t.number}</span>
              <div className="min-w-0">
                <strong>{t.title}</strong>{t.status === 'hidden' && <> <StatusPill status="insufficient">Hidden</StatusPill></>}
                <p className="topic-meta">{t.unit_number ? `Unit ${t.unit_number} · ` : ''}{t.clos.map((c) => c.code).join(', ')} · {t.summary}</p>
              </div>
              <div className="row">
                <Button variant="ghost" size="sm" icon={<Pencil />} aria-label={`Edit topic ${t.number}`} onClick={() => setForm({ id: t.id, title: t.title, summary: t.summary, clo_ids: t.clos.map((c) => c.id) })}>Edit</Button>
                <Button variant="ghost" size="sm" icon={<EyeOff />} aria-label={`${t.status === 'hidden' ? 'Show' : 'Hide'} topic ${t.number}`} onClick={() => toggle(t)}>{t.status === 'hidden' ? 'Show' : 'Hide'}</Button>
              </div>
            </li>
          ))}
        </ol>
      </Card>
      <Modal open={!!form} onClose={() => setForm(null)} title={form?.id ? 'Edit topic' : 'Add a topic'}
        footer={<><Button variant="secondary" onClick={() => setForm(null)}>Cancel</Button><Button loading={act.busy} onClick={save}>{form?.id ? 'Save topic' : 'Add topic'}</Button></>}>
        {form && (
          <>
            <Field label="Title">{(p) => <input {...p} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />}</Field>
            <Field label="What this topic teaches">{(p) => <textarea {...p} rows={3} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} />}</Field>
            <fieldset className="field">
              <legend>Outcomes this topic serves (the first one ticked is the main one)</legend>
              {clos.map((c) => (
                <label key={c.id} className="check-row"><input type="checkbox" checked={form.clo_ids.includes(c.id)}
                  onChange={(e) => setForm({ ...form, clo_ids: e.target.checked ? [...form.clo_ids, c.id] : form.clo_ids.filter((i) => i !== c.id) })} /> <strong>{c.code}</strong> {c.description}</label>
              ))}
            </fieldset>
            <Alert>{act.error}</Alert>
          </>
        )}
      </Modal>
    </>
  )
}
