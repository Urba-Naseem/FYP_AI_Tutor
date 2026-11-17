/** One course topic: the student's personal lesson for it (written for their level), revision notes, practice
 *  questions and the way to the previous / next topic. Opened from the path ("Study" steps) or the topic list. */
import { ArrowLeft, ArrowRight, CheckCircle2, ListChecks, RefreshCw, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { Button, Card, Segmented, Skeleton, StatusPill } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { Alert, EmptyState, useAction, useLoad } from '../../components/ui'
import { PracticePanel } from './PracticePanel'
import { TopicReading } from './ReadingPage'
import { SelectionPopover, TutorDrawer, type TutorRequest } from './TutorPanel'
import { SlideDeck, type Slide } from './SlideDeck'

type Mode = 'slides' | 'article'
const readMode = (): Mode => { try { return localStorage.getItem('lessonMode') === 'article' ? 'article' : 'slides' } catch { return 'slides' } }
const saveMode = (m: Mode) => { try { localStorage.setItem('lessonMode', m) } catch { /* storage unavailable */ } }

type Lesson = {
  id: number | null; topic_id: number; topic_title: string; topic_number: number; topic_summary: string; clo_id: number; clo_code: string
  description: string; level_label: string; reason: string; origin: string; lecture_md: string; notes_md: string; edited_by_instructor: boolean; slides: Slide[]
  previous_topic: { id: number; title: string } | null; next_topic: { id: number; title: string } | null
}
export type TopicRow = {
  id: number; number: number; title: string; summary: string; unit_number: number | null; unit_title: string | null
  clos: { id: number; code: string }[]; in_path: boolean; step_id: number | null; step_status: string | null; lesson_ready: boolean
}

export default function TopicPage() {
  const { courseId = '', topicId = '' } = useParams()
  const [params] = useSearchParams()
  const stepId = params.get('step')
  const nav = useNavigate()
  const [refresh, setRefresh] = useState(0)
  const [mode, setMode] = useState<Mode>(readMode)
  const [request, setRequest] = useState<TutorRequest | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(() => { try { return localStorage.getItem('tutorOpen') !== '0' && window.innerWidth >= 1100 } catch { return true } })
  const setDrawer = (v: boolean) => { setDrawerOpen(v); try { localStorage.setItem('tutorOpen', v ? '1' : '0') } catch { /* storage unavailable */ } }
  const l = useLoad(() => api<Lesson>(`/learn/${courseId}/topics/${topicId}/lesson${refresh ? '?refresh=true' : ''}`), [courseId, topicId, refresh])
  const act = useAction()
  const x = l.data
  const finish = async () => {
    if (stepId && (await act.run(() => api(`/learn/steps/${stepId}/complete`, { method: 'POST' }), 'Topic marked as done'))) nav(`/learn/${courseId}`)
  }
  return (
    <>
      <div className="phead">
        <div>
          <p className="phead-crumb"><Link to={`/learn/${courseId}`}>My course</Link> / <Link to={`/learn/${courseId}/units`}>Topics</Link> / Topic {x?.topic_number ?? ''}</p>
          <h1>{x ? x.topic_title : 'Topic'}</h1>
          {x && <p className="phead-desc">{x.clo_code} · {x.description}</p>}
        </div>
        {x && <div className="phead-actions"><StatusPill status="review">{x.level_label} level</StatusPill></div>}
      </div>
      {l.loading && !x ? <Skeleton lines={8} label="Writing your lesson — this can take a few seconds" /> : l.error ? <Alert onRetry={l.reload}>{l.error}</Alert> : !x ? (
        <EmptyState icon={ListChecks} title="Topic not found">Go back to your course and pick a topic from your path.</EmptyState>
      ) : (
        <div className={`reader${drawerOpen ? ' has-drawer' : ' has-rail'}`}>
          <div className="reader-main">
            <article className="topic-lesson reader-pane" aria-labelledby="lesson-title">
              <h2 id="lesson-title" className="sr-only">Your lesson</h2>
              {x.origin === 'offline'
                ? <Alert kind="info" title="Your personal lesson is not ready yet">The AI is offline or busy, so here is a short study guide. Your lesson will be written on your next visit.</Alert>
                : <p className="reader-meta">Written for you at the {x.level_label} level — {x.reason}.{x.edited_by_instructor && ' Reviewed by your instructor.'} Highlight any text to ask the tutor.</p>}
              {x.slides?.length > 0 && (
                <div className="reader-tools"><Segmented label="Show the lesson as" options={[{ value: 'slides', label: `Slides (${x.slides.length})` }, { value: 'article', label: 'Article' }]}
                  value={mode} onChange={(m) => { setMode(m); saveMode(m) }} /></div>
              )}
              {!x.slides?.length && x.origin !== 'offline' && (
                <p className="reader-meta">This lesson was written before slides were added. <button type="button" className="text-btn" onClick={() => setRefresh((n) => n + 1)}>Rewrite it with slides</button></p>
              )}
              {x.slides?.length > 0 && mode === 'slides' ? <SlideDeck slides={x.slides} title={x.topic_title} /> : <Markdown>{x.lecture_md}</Markdown>}
              {x.notes_md && <details className="reader-notes"><summary>Revision notes</summary><Markdown>{x.notes_md}</Markdown></details>}
              <div className="card-actions">
                {stepId && <Button icon={<CheckCircle2 />} loading={act.busy} onClick={finish}>Mark topic as done</Button>}
                {x.origin !== 'offline' && <Button variant="ghost" icon={<RefreshCw />} onClick={() => setRefresh((n) => n + 1)}>Explain it differently</Button>}
              </div>
              <Alert>{act.error}</Alert>
            </article>
            <TopicReading courseId={courseId} query={`${x.topic_title} ${x.description}`} />
            <PracticePanel key={x.topic_id} courseId={courseId} cloId={x.clo_id} topicId={x.topic_id} cloCode={x.clo_code} title="Practice this lecture" />
            <nav className="topic-nav" aria-label="Topics">
              {x.previous_topic ? <Link className="kbtn kbtn-secondary kbtn-md" to={`/learn/${courseId}/topic/${x.previous_topic.id}`}><ArrowLeft aria-hidden="true" />{x.previous_topic.title}</Link> : <span />}
              {x.next_topic && <Link className="kbtn kbtn-secondary kbtn-md" to={`/learn/${courseId}/topic/${x.next_topic.id}`}>{x.next_topic.title}<ArrowRight aria-hidden="true" /></Link>}
            </nav>
          </div>
          <TutorDrawer courseId={courseId} topicId={x.topic_id} request={request} open={drawerOpen} onClose={() => setDrawer(false)} />
          {!drawerOpen && <button type="button" className="tutor-rail" onClick={() => setDrawer(true)} aria-label="Open the AI tutor"><Sparkles aria-hidden="true" /><span>Ask the tutor</span></button>}
          <SelectionPopover onAction={async (a, text, section) => {
            if (a === 'note') { await act.run(() => api(`/tutor/${courseId}/notes`, { body: { topic_id: x.topic_id, kind: 'note', front: `> ${text}`, section } }), 'Saved to Notes & Cards'); return }
            setDrawer(true)
            setRequest({ mode: a === 'quiz' ? 'check' : 'explain', selection: text, key: Date.now() })
          }} />
        </div>
      )}
    </>
  )
}

/** All topics of the course, grouped by unit, with what is in the student's own path. */
export function TopicList({ courseId }: { courseId: string }) {
  const t = useLoad(() => api<TopicRow[]>(`/learn/${courseId}/topics`), [courseId])
  if (t.loading && !t.data) return <Skeleton lines={5} label="Loading the course topics" />
  if (t.error) return <Alert onRetry={t.reload}>{t.error}</Alert>
  if (!t.data?.length) return <p className="kempty">No topics yet — your instructor is still setting up this course.</p>
  const units = [...new Set(t.data.map((x) => x.unit_number ?? 0))]
  return (
    <>
      {units.map((u) => {
        const rows = t.data!.filter((x) => (x.unit_number ?? 0) === u)
        return (
          <Card key={u} title={u ? `Unit ${u}: ${rows[0].unit_title}` : 'Other topics'} subtitle={`${rows.length} topic${rows.length === 1 ? '' : 's'}`}>
            <ol className="topic-list">
              {rows.map((x) => (
                <li key={x.id} className={x.step_status === 'done' ? 'is-done' : undefined}>
                  <span className="topic-num" aria-hidden="true">{x.number}</span>
                  <div className="min-w-0">
                    <Link to={`/learn/${courseId}/topic/${x.id}${x.step_id ? `?step=${x.step_id}` : ''}`}><strong>{x.title}</strong></Link>
                    <p className="topic-meta">{x.clos.map((c) => c.code).join(', ')} · {x.summary}</p>
                  </div>
                  {x.step_status === 'done' ? <StatusPill status="attained">Done</StatusPill> : x.in_path ? <StatusPill status="review">In your path</StatusPill> : <StatusPill status="insufficient">Optional</StatusPill>}
                </li>
              ))}
            </ol>
          </Card>
        )
      })}
    </>
  )
}
