import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AuthShell } from '../../components/kit/AuthShell'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import PasswordInput from '../../components/PasswordInput'
import { Alert, useLoad } from '../../components/ui'

type Consent = { version: string; text: string; allowed_domain: string }

export function RegisterPage() {
  const consent = useLoad(() => api<Consent>('/auth/consent'))
  const programs = useLoad(() => api<{ code: string; name: string }[]>('/auth/programs'))
  const [f, setF] = useState({ student_number: '', full_name: '', email: '', password: '', password2: '', consent: false, program_code: '' })
  const [error, setError] = useState('')
  const [done, setDone] = useState('')
  const [mismatch, setMismatch] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault()
    setMismatch(f.password !== f.password2)
    if (f.password !== f.password2) return
    setError('')
    try {
      const r = await api<{ message: string }>('/auth/register', { body: { ...f, consent_version: consent.data?.version } })
      setDone(r.message)
    } catch (err) {
      setError((err as Error).message)
    }
  }
  return (
    <AuthShell wide="medium">
      <div className="brand"><span className="brand-mark" aria-hidden="true">A</span>AI Tutor</div>
      <div className="card">
        <h1>Student registration</h1>
        {done ? (
          <Alert kind="success">{done} <Link to={`/dev/outbox?to=${encodeURIComponent(f.email.trim())}`}>Open your demo mailbox</Link></Alert>
        ) : (
          <form onSubmit={submit}>
            <div className="field"><label htmlFor="rn">Full name</label><input id="rn" required autoComplete="name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></div>
            {(programs.data?.length ?? 0) > 0 && (
              <div className="field"><label htmlFor="rd">Department</label>
                <select id="rd" required aria-describedby="rd-hint" value={f.program_code} onChange={(e) => setF({ ...f, program_code: e.target.value })}>
                  <option value="">Choose your department…</option>
                  {programs.data!.map((p) => <option key={p.code} value={p.code}>{p.name} ({p.code})</option>)}
                </select>
                <p id="rd-hint" className="field-hint">You see the courses and semester roadmap of this department. Only an administrator can change it later.</p>
              </div>
            )}
            <div className="field"><label htmlFor="rs">Student number</label><input id="rs" required value={f.student_number} onChange={(e) => setF({ ...f, student_number: e.target.value })} placeholder="e.g. BSCS-2026-014" /></div>
            <div className="field"><label htmlFor="re">Institutional e-mail</label><input id="re" type="email" required autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder={`name@${consent.data?.allowed_domain ?? 'university.edu'}`} /></div>
            <div className="row items-start">
              <div className="field flex-field">
                <label htmlFor="rp">Password</label>
                <PasswordInput id="rp" required autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} aria-describedby="rp-hint" />
                <p id="rp-hint" className="field-hint">At least 10 characters with letters and digits.</p>
              </div>
              <div className="field flex-field">
                <label htmlFor="rp2">Repeat password</label>
                <PasswordInput id="rp2" required autoComplete="new-password" value={f.password2} onChange={(e) => setF({ ...f, password2: e.target.value })} aria-invalid={mismatch || undefined} aria-describedby={mismatch ? 'rp2-error' : undefined} />
                {mismatch && <p id="rp2-error" className="field-error" role="alert">Passwords do not match. Type the same password in both fields.</p>}
              </div>
            </div>
            {consent.data && (
              <>
                <p id="consent-title" className="field-title">Consent (version {consent.data.version})</p>
                <pre className="plain max-h-sm" tabIndex={0} aria-labelledby="consent-title">{consent.data.text}</pre>
                <label className="inline-check"><input type="checkbox" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} /> I have read and accept this consent statement.</label>
              </>
            )}
            <Alert>{error}</Alert>
            <button disabled={!f.consent}>Create account</button>
          </form>
        )}
        <p className="small"><Link to="/login">Back to sign in</Link></p>
      </div>
    </AuthShell>
  )
}

export function VerifyEmailPage() {
  const [params] = useSearchParams()
  const [state, setState] = useState<'working' | 'ok' | string>('working')
  // a verification link works once: send it once per token, even when React runs the effect twice in development
  const sent = useRef<string | null>(null)
  useEffect(() => {
    const token = params.get('token')
    if (sent.current === token) return
    sent.current = token
    api('/auth/verify-email', { body: { token } })
      .then(() => setState('ok'))
      .catch((e) => setState((e as Error).message))
  }, [params])
  return (
    <AuthShell>
      <div className="brand"><span className="brand-mark" aria-hidden="true">A</span>AI Tutor</div>
      <div className="card">
        <h1>E-mail verification</h1>
        {state === 'working' ? <p>Verifying…</p> : state === 'ok' ? <Alert kind="success">Your e-mail is verified. <Link to="/login">Sign in</Link></Alert> : <Alert>{state}</Alert>}
      </div>
    </AuthShell>
  )
}
