import { useState, type FormEvent } from 'react'
import { AuthShell } from '../../components/kit/AuthShell'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import PasswordInput from '../../components/PasswordInput'
import { Alert } from '../../components/ui'

export default function LoginPage() {
  const { login } = useAuth()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(email, password)
      nav('/')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthShell>
      <div className="brand"><span className="brand-mark" aria-hidden="true">A</span>AI Tutor</div>
      <div className="card">
        <h1>Sign in</h1>
        <p className="muted small">Use your institutional e-mail and password.</p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="email">Institutional e-mail</label>
            <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!error || undefined} aria-describedby={error ? 'login-error' : undefined} />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <PasswordInput id="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error || undefined} aria-describedby={error ? 'login-error' : undefined} />
          </div>
          <Alert id="login-error">{error}</Alert>
          <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </div>
      <nav className="auth-links" aria-label="Account help">
        <Link to="/forgot-password">Forgot password?</Link>
        <Link to="/register">New student? Register</Link>
      </nav>
    </AuthShell>
  )
}
