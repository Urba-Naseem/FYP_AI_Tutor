/** Suggested reading: the approved teaching sources of the student's course (textbooks, notes, slides). Students
 *  can open them whenever they want; topic pages show the matching passages. */
import { BookOpen, Download, FileText, Library, Presentation } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api, download } from '../../api/client'
import { Button, Card, Skeleton } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, EmptyState, useAction, useLoad } from '../../components/ui'

type Src = { id: number; title: string; kind: string; filename: string; version: number; license_note: string | null }
type Passage = { source_id: number; source_title: string; heading: string | null; text: string }
const KIND: Record<string, { label: string; Icon: typeof BookOpen }> = {
  textbook: { label: 'Textbook', Icon: BookOpen }, lecture_notes: { label: 'Lecture notes', Icon: FileText },
  slides: { label: 'Slides', Icon: Presentation }, reference: { label: 'Reference', Icon: Library }, other: { label: 'Reading', Icon: FileText },
}

export default function ReadingPage() {
  const { courseId = '' } = useParams()
  const d = useLoad(() => api<{ sources: Src[] }>(`/learn/${courseId}/readings`), [courseId])
  const act = useAction()
  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb"><Link to={`/learn/${courseId}`}>My course</Link> / Suggested reading</p>
          <h1>Suggested reading</h1>
          <p className="phead-desc">Material your instructor approved for this course. Your topic lessons point to the right pages, and when the AI tutor is offline your lessons are taught from it.</p>
        </div>
      </div>
      <Alert>{act.error}</Alert>
      {d.loading && !d.data ? <Skeleton lines={4} label="Loading the reading list" /> : d.error ? <Alert onRetry={d.reload}>{d.error}</Alert> : !d.data?.sources.length ? (
        <EmptyState icon={Library} title="No suggested reading yet">Your instructor has not added reading for this course yet.</EmptyState>
      ) : (
        <ul className="reading-list">
          {d.data.sources.map((s) => {
            const k = KIND[s.kind] ?? KIND.other
            return (
              <li key={s.id} className="kcard">
                <span className="reading-icon" aria-hidden="true"><k.Icon /></span>
                <div className="min-w-0">
                  <p className="reading-title">{s.title}</p>
                  <p className="topic-meta">{k.label}{s.version > 1 ? ` · version ${s.version}` : ''} · {s.filename}{s.license_note ? ` · ${s.license_note}` : ''}</p>
                </div>
                <Button variant="secondary" size="sm" icon={<Download />} loading={act.busy} aria-label={`Download ${s.title}`}
                  onClick={() => act.run(() => download(`/learn/${courseId}/readings/${s.id}/file`, s.filename))}>Download</Button>
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

/** Topic page: the passages of the suggested reading that match this topic. */
export function TopicReading({ courseId, query }: { courseId: string; query: string }) {
  const d = useLoad(() => api<{ sources: Src[]; passages: Passage[] }>(`/learn/${courseId}/readings?q=${encodeURIComponent(query)}`), [courseId, query])
  if (!d.data?.sources.length) return null
  return (
    <Card title="Suggested reading for this topic" subtitle="From the material your instructor approved"
      action={<Link className="kbtn kbtn-secondary kbtn-sm" to={`/learn/${courseId}/reading`}>All reading</Link>}>
      {!d.data.passages.length ? <p className="kempty">No passage matches this topic closely — open the full reading list.</p> : (
        <div className="reading-passages">
          {d.data.passages.map((p, i) => (
            <details key={i} open={i === 0}>
              <summary><strong>{p.heading || p.source_title}</strong> <span className="small muted">· {p.source_title}</span></summary>
              <Markdown>{p.text}</Markdown>
            </details>
          ))}
        </div>
      )}
    </Card>
  )
}
