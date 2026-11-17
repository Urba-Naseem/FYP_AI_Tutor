/** Lecture-reader tutor: a selection popover over the lesson (Explain / Quiz Me / Save Note) and a collapsible
 *  right-hand drawer (bottom sheet on phones) with contextual replies, section badges that scroll to and briefly
 *  highlight the source, a Socratic quiz card (Hint; Reveal locked until an attempt) and Notes & Cards. */
import { BookmarkPlus, Brain, Layers, Lightbulb, MessageCircleQuestion, PanelRightClose, Send, Sparkles, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { Button, Segmented } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'
import { slugify } from '../../components/slug'
import { Alert, useAction, useLoad } from '../../components/ui'

type Cited = { n: number; title: string }
type Reply = { reply_md: string; cited: Cited[]; question: string; flashcard: { front: string; back: string } | null; offline: boolean }
type Turn = { role: 'student' | 'tutor'; text: string }
type Entry = { id: number; quote?: string; ask: string; reply: Reply; mode: string; attempted?: boolean; attempt?: string }
type Note = { id: number; kind: string; front: string; back: string; section: string | null }
export type TutorRequest = { mode: 'explain' | 'check'; selection: string; key: number }

/** Smoothly scroll to a lesson section and flash it (no animation under reduced motion). */
function goToSection(title: string) {
  const el = document.getElementById(slugify(title))
  if (!el) return
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  el.classList.add('section-flash')
  window.setTimeout(() => el.classList.remove('section-flash'), 1600)
}

/** The nearest section heading above the selection (for notes). */
function sectionOf(node: Node | null): string | undefined {
  let el = node instanceof Element ? node : node?.parentElement ?? null
  while (el && !el.classList?.contains('topic-lesson')) {
    let p: Element | null = el
    while (p) { if (p.tagName === 'H2') return p.textContent ?? undefined; p = p.previousElementSibling }
    el = el.parentElement
  }
  return undefined
}

/** Floating toolbar over highlighted lesson text. */
export function SelectionPopover({ onAction }: { onAction: (a: 'explain' | 'quiz' | 'note', text: string, section?: string) => void }) {
  const [box, setBox] = useState<{ x: number; y: number; text: string; section?: string } | null>(null)
  useEffect(() => {
    const onUp = () => window.setTimeout(() => {
      const sel = window.getSelection()
      const text = sel?.toString().trim() ?? ''
      const inLesson = !!sel?.anchorNode && !!sel.anchorNode.parentElement?.closest('.topic-lesson')
      if (!sel || sel.rangeCount === 0 || text.length < 4 || !inLesson) { setBox(null); return }
      const r = sel.getRangeAt(0).getBoundingClientRect()
      setBox({ x: Math.min(Math.max(r.left + r.width / 2, 140), window.innerWidth - 140), y: Math.max(r.top - 8, 56), text: text.slice(0, 1200), section: sectionOf(sel.anchorNode) })
    }, 0)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setBox(null) }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('keyup', onUp)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mouseup', onUp); document.removeEventListener('keyup', onUp); document.removeEventListener('keydown', onKey) }
  }, [])
  if (!box) return null
  const act = (a: 'explain' | 'quiz' | 'note') => { onAction(a, box.text, box.section); setBox(null); window.getSelection()?.removeAllRanges() }
  return (
    <div className="sel-pop" role="toolbar" aria-label="Selected text" style={{ left: box.x, top: box.y }} onMouseDown={(e) => e.preventDefault()}>
      <button type="button" onClick={() => act('explain')}><Sparkles aria-hidden="true" />Explain</button>
      <button type="button" onClick={() => act('quiz')}><Brain aria-hidden="true" />Quiz me</button>
      <button type="button" onClick={() => act('note')}><BookmarkPlus aria-hidden="true" />Save note</button>
    </div>
  )
}

export function TutorDrawer({ courseId, topicId, request, open, onClose }: { courseId: string; topicId: number; request: TutorRequest | null; open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<'tutor' | 'notes'>('tutor')
  const [entries, setEntries] = useState<Entry[]>([])
  const [text, setText] = useState('')
  const act = useAction()
  const notes = useLoad(() => api<Note[]>(`/tutor/${courseId}/notes?topic_id=${topicId}`), [courseId, topicId])
  const seq = useRef(0)
  const end = useRef<HTMLDivElement>(null)
  const self = useRef<HTMLElement>(null)
  // on narrow screens the drawer sits in the page above the lesson: bring it into view when it opens
  useEffect(() => {
    if (open && window.innerWidth < 1100) self.current?.scrollIntoView({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }, [open, request?.key])

  const history = (upto = entries): Turn[] => upto.flatMap((e) => [
    { role: 'student' as const, text: e.ask }, { role: 'tutor' as const, text: e.reply.reply_md },
    ...(e.attempt ? [{ role: 'student' as const, text: `Answer: ${e.attempt}` }] : []),
  ])
  const ask = async (mode: string, opts: { selection?: string; message?: string; label: string; quote?: string; extra?: Turn[] }) => {
    const r = await act.run(() => api<Reply>(`/tutor/${courseId}/topics/${topicId}`, { body: { mode, selection: opts.selection ?? null, message: opts.message ?? null, history: [...history(), ...(opts.extra ?? [])] } }))
    if (!r) return
    setEntries((es) => [...es, { id: ++seq.current, quote: opts.quote, ask: opts.label, reply: r, mode }])
    setTab('tutor')
    window.setTimeout(() => end.current?.scrollIntoView({ block: 'end' }), 50)
  }
  // a new request from the selection popover
  useEffect(() => {
    if (!request) return
    if (request.mode === 'explain') ask('explain', { selection: request.selection, label: 'Explain this', quote: request.selection })
    else ask('check', { selection: request.selection, label: 'Quiz me on this', quote: request.selection })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request?.key])

  const saveNote = async (front: string, back = '', section?: string, kind: 'note' | 'flashcard' = 'note') => {
    if (await act.run(() => api(`/tutor/${courseId}/notes`, { body: { topic_id: topicId, kind, front, back, section } }), kind === 'note' ? 'Saved to Notes & Cards' : 'Flashcard saved')) notes.reload()
  }
  const submitAttempt = async (e: Entry, attempt: string) => {
    setEntries((es) => es.map((x) => (x.id === e.id ? { ...x, attempted: true, attempt } : x)))
    await ask('answer', { message: attempt, label: `My answer: ${attempt}`, extra: [{ role: 'student', text: `Answer: ${attempt}` }] })
  }

  return (
    <aside ref={self} className={`tutor-drawer${open ? ' is-open' : ''}`} aria-label="AI tutor" aria-hidden={!open || undefined}>
      <div className="tutor-drawer-head">
        <strong>Tutor</strong>
        <Segmented label="Tutor view" options={[{ value: 'tutor', label: 'Ask' }, { value: 'notes', label: `Notes & Cards${notes.data?.length ? ` · ${notes.data.length}` : ''}` }]} value={tab} onChange={setTab} />
        <button type="button" className="icon-ghost" aria-label="Close the tutor" onClick={onClose}><PanelRightClose aria-hidden="true" /></button>
      </div>
      {tab === 'tutor' ? (
        <div className="tutor-drawer-body">
          {!entries.length && <p className="tutor-empty">Highlight any text in the lesson to have it explained, quiz yourself, or save it. Or ask a question below — your tutor guides you to the answer with hints and questions rather than just telling you.</p>}
          {entries.map((e) => <ReplyCard key={e.id} e={e} busy={act.busy}
            onPill={(how) => ask('explain', { selection: e.quote ?? e.reply.reply_md.slice(0, 600), message: how === 'simpler' ? 'Explain it more simply, in plain words.' : 'Explain it with an everyday analogy.', label: how === 'simpler' ? 'Simpler' : 'Analogy', quote: e.quote })}
            onHint={() => ask('hint', { label: 'Hint please' })} onReveal={() => ask('reveal', { label: 'Reveal the answer' })} onAttempt={(a) => submitAttempt(e, a)}
            onStuck={() => ask('ask', { message: "I'm stuck - please explain it to me.", label: "I'm stuck — explain it" })}
            onSave={(front, back, kind) => saveNote(front, back, e.reply.cited[0]?.title, kind)} />)}
          <div ref={end} />
          <Alert>{act.error}</Alert>
        </div>
      ) : <NotesList notes={notes.data ?? []} onDelete={async (id) => { if (await act.run(() => api(`/tutor/${courseId}/notes/${id}`, { method: 'DELETE' }), 'Deleted')) notes.reload() }} />}
      {tab === 'tutor' && (
        <form className="tutor-input" onSubmit={(ev) => { ev.preventDefault(); if (text.trim()) { ask('ask', { message: text.trim(), label: text.trim() }); setText('') } }}>
          <label htmlFor="tutor-text" className="sr-only">Ask about this lesson, or reply to your tutor</label>
          <input id="tutor-text" value={text} onChange={(ev) => setText(ev.target.value)} placeholder={entries.at(-1)?.mode === 'ask' && entries.at(-1)?.reply.question ? 'Your thinking…' : 'Ask about this lesson…'} />
          <Button type="submit" size="sm" icon={<Send />} loading={act.busy} disabled={!text.trim()} aria-label="Ask the tutor">Ask</Button>
        </form>
      )}
    </aside>
  )
}

function ReplyCard({ e, busy, onPill, onHint, onReveal, onAttempt, onSave, onStuck }: {
  e: Entry; busy: boolean; onPill: (how: 'simpler' | 'analogy') => void; onHint: () => void; onReveal: () => void
  onAttempt: (a: string) => void; onSave: (front: string, back: string, kind: 'note' | 'flashcard') => void; onStuck: () => void
}) {
  const [attempt, setAttempt] = useState('')
  const [card, setCard] = useState<{ front: string; back: string } | null>(null)
  const isQuiz = e.mode === 'check' && !!e.reply.question
  return (
    <article className="reply">
      {e.quote && <p className="reply-quote" title={e.quote}>“{e.quote}”</p>}
      {!e.quote && <p className="reply-ask">{e.ask}</p>}
      {isQuiz ? (
        <div className="quiz-card">
          <p className="quiz-q"><MessageCircleQuestion aria-hidden="true" /><span><Markdown>{e.reply.question}</Markdown></span></p>
          {!e.attempted ? (
            <form onSubmit={(ev) => { ev.preventDefault(); if (attempt.trim()) onAttempt(attempt.trim()) }}>
              <label className="sr-only" htmlFor={`att-${e.id}`}>Your answer</label>
              <textarea id={`att-${e.id}`} rows={2} value={attempt} onChange={(ev) => setAttempt(ev.target.value)} placeholder="Your answer…" />
              <div className="quiz-actions">
                <Button type="button" variant="ghost" size="sm" icon={<Lightbulb />} onClick={onHint} disabled={busy}>Hint</Button>
                <Button type="button" variant="ghost" size="sm" disabled title="Submit an attempt first">Reveal answer</Button>
                <Button type="submit" size="sm" disabled={!attempt.trim() || busy}>Submit</Button>
              </div>
            </form>
          ) : (
            <div className="quiz-actions"><span className="small muted">Your answer: {e.attempt}</span><Button variant="ghost" size="sm" onClick={onReveal} disabled={busy}>Reveal answer</Button></div>
          )}
        </div>
      ) : <Markdown>{e.reply.reply_md}</Markdown>}
      {e.reply.offline && <p className="small muted m-0">Tutor offline — showing the matching part of your lesson.</p>}
      <div className="reply-meta">
        {e.reply.cited.map((c) => <button key={c.n} type="button" className="sec-badge" onClick={() => goToSection(c.title)} aria-label={`Go to section ${c.n}: ${c.title}`}>§ {c.n}</button>)}
        {e.mode === 'ask' && !!e.reply.question && !e.reply.offline && <button type="button" className="pill" onClick={onStuck} disabled={busy}>I'm stuck — explain it</button>}
        {!isQuiz && !e.reply.offline && e.mode !== 'ask' && <>
          <button type="button" className="pill" onClick={() => onPill('simpler')} disabled={busy}>Simpler</button>
          <button type="button" className="pill" onClick={() => onPill('analogy')} disabled={busy}>Analogy</button>
        </>}
      </div>
      {!isQuiz && (
        <div className="reply-foot">
          <button type="button" className="foot-btn" onClick={() => onSave(e.reply.reply_md, '', 'note')}><BookmarkPlus aria-hidden="true" />Save note</button>
          <button type="button" className="foot-btn" onClick={() => setCard(card ? null : { front: e.reply.flashcard?.front ?? e.quote ?? '', back: e.reply.flashcard?.back ?? '' })}><Layers aria-hidden="true" />Flashcard</button>
        </div>
      )}
      {card && (
        <form className="card-editor" onSubmit={(ev) => { ev.preventDefault(); if (card.front.trim()) { onSave(card.front, card.back, 'flashcard'); setCard(null) } }}>
          <label htmlFor={`f-${e.id}`}>Front</label><input id={`f-${e.id}`} value={card.front} onChange={(ev) => setCard({ ...card, front: ev.target.value })} />
          <label htmlFor={`b-${e.id}`}>Back</label><input id={`b-${e.id}`} value={card.back} onChange={(ev) => setCard({ ...card, back: ev.target.value })} />
          <div className="quiz-actions"><Button type="button" variant="ghost" size="sm" icon={<X />} onClick={() => setCard(null)}>Cancel</Button><Button type="submit" size="sm" disabled={!card.front.trim()}>Save card</Button></div>
        </form>
      )}
    </article>
  )
}

function NotesList({ notes, onDelete }: { notes: Note[]; onDelete: (id: number) => void }) {
  const [flipped, setFlipped] = useState<Record<number, boolean>>({})
  if (!notes.length) return <p className="tutor-empty">No notes or cards yet. Save them from a reply or from highlighted text.</p>
  return (
    <ul className="tutor-notes">
      {notes.map((n) => (
        <li key={n.id} className={`tutor-note is-${n.kind}`}>
          {n.kind === 'flashcard' ? (
            <button type="button" className="flashcard" aria-pressed={!!flipped[n.id]} onClick={() => setFlipped({ ...flipped, [n.id]: !flipped[n.id] })}>
              <span className="small muted">{flipped[n.id] ? 'Back' : 'Front · press to turn'}</span><Markdown>{flipped[n.id] ? n.back || '—' : n.front}</Markdown>
            </button>
          ) : <Markdown>{n.front}</Markdown>}
          <div className="tutor-note-foot">
            {n.section ? <button type="button" className="sec-badge" onClick={() => goToSection(n.section!)}>§ {n.section}</button> : <span />}
            <button type="button" className="icon-ghost" aria-label="Delete" onClick={() => onDelete(n.id)}><Trash2 aria-hidden="true" /></button>
          </div>
        </li>
      ))}
    </ul>
  )
}
