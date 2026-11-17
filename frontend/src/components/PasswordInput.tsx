import { Eye, EyeOff } from 'lucide-react'
import { useState, type InputHTMLAttributes } from 'react'

/** Password field with an eye icon to show / hide the password (UI rules §6). Takes the same props as <input>. */
export default function PasswordInput(props: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & { id: string }) {
  const [shown, setShown] = useState(false)
  const label = shown ? 'Hide password' : 'Show password'
  return (
    <div className="password-input">
      <input {...props} type={shown ? 'text' : 'password'} />
      <button type="button" className="password-toggle" aria-label={label} title={label} aria-pressed={shown} aria-controls={props.id}
        onClick={() => setShown(!shown)}>
        {shown ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
      </button>
    </div>
  )
}
