/** A lesson presented as slides: title, key points, an optional visual (diagram / code / table / maths, drawn by
 *  the Markdown renderer - no image files) and the lecturer's explanation under it. Next / Previous buttons, the
 *  arrow keys (when the deck has focus) and numbered dots; the slide number is announced to screen readers. */
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'
import { Button } from '../../components/kit/Kit'
import Markdown from '../../components/Markdown'

export type Slide = { title: string; points: string[]; visual: string; explanation: string }

export function SlideDeck({ slides, title }: { slides: Slide[]; title: string }) {
  const [i, setI] = useState(0)
  const n = slides.length
  const go = (k: number) => setI(Math.max(0, Math.min(n - 1, k)))
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight' || e.key === 'PageDown') { e.preventDefault(); go(i + 1) }
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(i - 1) }
    if (e.key === 'Home') { e.preventDefault(); go(0) }
    if (e.key === 'End') { e.preventDefault(); go(n - 1) }
  }
  const s = slides[i]
  return (
    <section className="deck" aria-roledescription="slide deck" aria-label={`${title} slides`} tabIndex={0} onKeyDown={onKey}>
      <p className="sr-only" aria-live="polite">Slide {i + 1} of {n}: {s.title}</p>
      <div className="deck-slide" key={i}>
        <p className="deck-count">Slide {i + 1} / {n}</p>
        <h3 className="deck-title">{s.title}</h3>
        <div className={`deck-body${s.visual ? ' has-visual' : ''}`}>
          {s.points.length > 0 && <ul className="deck-points">{s.points.map((p, k) => <li key={k}><Markdown>{p}</Markdown></li>)}</ul>}
          {s.visual && <div className="deck-visual"><Markdown>{s.visual}</Markdown></div>}
        </div>
      </div>
      {s.explanation && <div className="deck-explain"><Markdown>{s.explanation}</Markdown></div>}
      <div className="deck-nav">
        <Button variant="secondary" icon={<ChevronLeft />} onClick={() => go(i - 1)} disabled={i === 0}>Previous</Button>
        <ol className="deck-dots" aria-label="Go to slide">
          {slides.map((sl, k) => (
            <li key={k}><button type="button" className="deck-dot" aria-current={k === i ? 'step' : undefined} aria-label={`Slide ${k + 1}: ${sl.title}`} onClick={() => go(k)} /></li>
          ))}
        </ol>
        <Button variant={i === n - 1 ? 'secondary' : 'primary'} onClick={() => go(i + 1)} disabled={i === n - 1}>Next<ChevronRight aria-hidden="true" /></Button>
      </div>
    </section>
  )
}
