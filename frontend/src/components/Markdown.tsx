import { isValidElement, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { slugify, textOf } from './slug'

/** Renders a Mermaid diagram, with the source as an accessible text alternative. */
function Mermaid({ code }: { code: string }) {
  const id = useId().replace(/:/g, '')
  const [svg, setSvg] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    let alive = true
    import('mermaid').then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })
      try {
        const out = await mermaid.render(`m${id}`, code)
        if (alive) setSvg(out.svg)
      } catch (e) {
        if (alive) setErr(String(e))
      }
    })
    return () => { alive = false }
  }, [code, id])
  if (err) return <pre className="plain" tabIndex={0}>{code}</pre>
  return (
    <figure>
      <div className="mermaid-box" tabIndex={0} role="img" aria-label={`Diagram: ${code.replace(/\s+/g, ' ').slice(0, 200)}`} dangerouslySetInnerHTML={{ __html: svg }} />
      <figcaption>Diagram — a text version is available below.</figcaption>
      <details><summary className="small">Diagram as text</summary><pre className="plain" tabIndex={0}>{code}</pre></details>
    </figure>
  )
}

/** Dark Espresso card for code and display maths, with a Copy button (copies TeX source for maths). */
function CopyCard({ kind, children }: { kind: 'code' | 'math'; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)
  async function copy() {
    const el = ref.current
    if (!el) return
    const tex = el.querySelector('annotation[encoding="application/x-tex"]')?.textContent
    try {
      await navigator.clipboard.writeText(tex ?? el.innerText ?? el.textContent ?? '')
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }
  return (
    <div className={`code-card ${kind}`}>
      <button type="button" className="code-copy" onClick={copy}>
        {copied ? 'Copied' : 'Copy'}<span className="sr-only">{kind === 'math' ? ' LaTeX' : ' code'}</span>
      </button>
      <span className="sr-only" role="status">{copied ? (kind === 'math' ? 'LaTeX copied' : 'Code copied') : ''}</span>
      <div ref={ref} className="code-card-body" tabIndex={0} role="region" aria-label={kind === 'math' ? 'Formula' : 'Code example'}>{children}</div>
    </div>
  )
}

/** Drop react-markdown's hast `node` prop so it is not forwarded to the DOM. */
function withoutNode<T extends { node?: unknown }>(props: T): Omit<T, 'node'> {
  const { node, ...rest } = props
  void node
  return rest
}

const isMermaid = (node: ReactNode) =>
  isValidElement<{ className?: string }>(node) && !!node.props.className?.includes('language-mermaid')

/** Markdown with LaTeX maths (KaTeX), syntax-highlighted code, tables and Mermaid diagrams. */
/** `inline` renders paragraphs as spans, for short text inside a line (answer options, labels). */
export default function Markdown({ children, inline }: { children: string; inline?: boolean }) {
  const Wrap = inline ? 'span' : 'div'
  return (
    <Wrap className={inline ? 'markdown markdown-inline' : 'markdown'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
        components={{
          pre(props) {
            const { children: c, ...rest } = withoutNode(props)
            const child = Array.isArray(c) ? c[0] : c
            if (isMermaid(child)) return <>{c}</>
            return <CopyCard kind="code"><pre {...rest}>{c}</pre></CopyCard>
          },
          span(props) {
            const { className, children: c, ...rest } = withoutNode(props)
            const el = <span className={className} {...rest}>{c}</span>
            return className?.split(' ').includes('katex-display') ? <CopyCard kind="math">{el}</CopyCard> : el
          },
          h2(props) {
            const { children: c, ...rest } = withoutNode(props)
            return <h2 id={slugify(textOf(c))} {...rest}>{c}</h2>
          },
          h3(props) {
            const { children: c, ...rest } = withoutNode(props)
            return <h3 id={slugify(textOf(c))} {...rest}>{c}</h3>
          },
          ...(inline ? { p(props: { children?: ReactNode }) { return <span>{props.children}</span> } } : {}),
          code(props) {
            const { className, children: c, ...rest } = withoutNode(props)
            if (className?.includes('language-mermaid')) return <Mermaid code={String(c).trim()} />
            return <code className={className} {...rest}>{c}</code>
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </Wrap>
  )
}
