import { Bell, ChevronUp, FileText, Home, LogOut, Minus, Monitor, Moon, PanelLeft, Plus, Sun, UserCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './api/client'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { DialogHost } from './components/Dialog'
import RouteAnnouncer from './components/RouteAnnouncer'
import ToastHost from './components/Toasts'
import { UploadBanner } from './components/UploadBanner'
import { NAV_ICON } from './components/navIcons'
import { Loading } from './components/ui'
import LoginPage from './pages/auth/LoginPage'
import { DevOutboxPage, ForgotPasswordPage, ResetPasswordPage } from './pages/auth/PasswordPages'
import { publicRoutes } from './publicRoutes'
import { routes } from './routes'

const read = (key: string) => { try { return localStorage.getItem(key) } catch { return null } }
const write = (key: string, value: string) => { try { localStorage.setItem(key, value) } catch { /* storage unavailable */ } }

type Theme = 'system' | 'light' | 'dark'

/** Viewer display preferences: theme (light / dark / follow system), high contrast and text size. */
function useDisplayPrefs() {
  const [contrast, setContrast] = useState(() => read('contrast') === 'high')
  const [scale, setScale] = useState(() => Number(read('fontScale') || 1))
  const [theme, setTheme] = useState<Theme>(() => (read('theme') as Theme) || 'system')
  useEffect(() => {
    const root = document.documentElement
    root.dataset.contrast = contrast ? 'high' : 'normal'
    root.style.setProperty('--font-scale', String(scale))
    if (theme === 'system') delete root.dataset.theme
    else root.dataset.theme = theme
    write('contrast', contrast ? 'high' : 'normal')
    write('fontScale', String(scale))
    write('theme', theme)
  }, [contrast, scale, theme])
  return { contrast, setContrast, scale, setScale, theme, setTheme }
}

/** Matches the CSS breakpoint where the sidebar becomes a persistent column (brief: drawer below 860px). */
const isWide = () => typeof window.matchMedia !== 'function' || window.matchMedia('(min-width: 860px)').matches

function useNavOpen() {
  const [open, setOpen] = useState(() => (isWide() ? read('navOpen') !== '0' : false))
  // follow the breakpoint: shrinking the window (or turning a tablet) must not leave the drawer open over the page
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(min-width: 860px)')
    const onChange = () => setOpen(mq.matches ? read('navOpen') !== '0' : false)
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])
  function toggle() {
    setOpen((o) => {
      if (isWide()) write('navOpen', o ? '0' : '1')
      return !o
    })
  }
  return { open, toggle, close: () => setOpen(false) }
}

/** Student bottom bar on phones (DESIGN.md §5: up to 5 key items). */
const STUDENT_BOTTOM = [
  { to: '/', label: 'Home' }, { to: '/learn', label: 'Learning' }, { to: '/courses', label: 'Courses' },
  { to: '/notifications', label: 'Alerts' }, { to: '/account', label: 'Account' },
]

/** Brand: a notebook page with the red margin line and an "A". */
function Brand({ sub }: { sub?: boolean }) {
  return (
    <span className="brand">
      <span className="brand-mark" aria-hidden="true">A</span>
      <span className="brand-text">AI Tutor{sub && <span className="brand-sub">Supervised learning</span>}</span>
    </span>
  )
}

/** Sidebar groups and labels per role, as in the mockups (routes and permissions are unchanged). */
const NAV_LAYOUT: Partial<Record<string, Record<string, [group: string, label: string]>>> = {
  student: {
    '/': ['Learn', 'Home'], '/learn': ['Learn', 'My courses'], '/roadmap': ['Learn', 'My roadmap'], '/courses': ['Learn', 'Courses & enrolment'],
    '/notifications': ['Account', 'Notifications'], '/my-appeals': ['Account', 'Appeals'], '/account': ['Account', 'My account'],
  },
  instructor: {
    '/console': ['Teach', 'Console'], '/reviews': ['Teach', 'Review queue'], '/appeals': ['Teach', 'Appeals'],
    '/sources': ['Content', 'Teaching sources'], '/topics': ['Content', 'Course topics'], '/items': ['Content', 'Question bank'], '/content': ['Content', 'Learning content'],
    '/outcomes/review': ['Curriculum', 'Outcome review'], '/criteria': ['Curriculum', 'Attainment criteria'], '/mastery-policy': ['Curriculum', 'Mastery rules'],
    '/mappings': ['Curriculum', 'Mappings & activation'],
    '/': ['Account', 'Home'], '/notifications': ['Account', 'Notifications'], '/account': ['Account', 'My account'],
  },
}

/** Count badges in the sidebar: unread notifications; for staff also pending reviews and open appeals. Re-read on
 *  every page change. Read-only calls to existing endpoints; a failed call just shows no badge. */
function useNavCounts(role: string, pathname: string) {
  const [counts, setCounts] = useState<Record<string, number>>({})
  useEffect(() => {
    if (!role) return
    let stop = false
    const staff = role === 'instructor'  // review queue and appeals are instructor work
    const get = async <T,>(p: string): Promise<T | null> => { try { return await api<T>(p) } catch { return null } }
    ;(async () => {
      const [notes, reviews, appeals] = await Promise.all([
        get<{ read: boolean }[]>('/notifications'),
        staff ? get<unknown[]>('/reviews') : Promise.resolve(null),
        staff ? get<{ status: string }[]>('/appeals') : Promise.resolve(null),
      ])
      if (stop) return
      setCounts({
        '/notifications': notes?.filter((n) => !n.read).length ?? 0,
        '/reviews': reviews?.length ?? 0,
        '/appeals': appeals?.filter((a) => !['upheld', 'rejected', 'resolved', 'withdrawn', 'closed'].includes(a.status)).length ?? 0,
      })
    })()
    return () => { stop = true }
  }, [role, pathname])
  return counts
}

const ROLE_LABEL: Record<string, string> = { student: 'Student', instructor: 'Instructor', admin: 'Administrator', exam_staff: 'Exam office' }

type Prefs = ReturnType<typeof useDisplayPrefs>

const initials = (name: string) => name.replace(/^(Dr|Prof|Mr|Ms|Mrs)\.?\s+/i, '').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

/** Profile menu (disclosure pattern): Escape or an outside click closes it and focus returns to the button. */
function ProfileMenu({ name, role, detail, onSignOut, prefs }: { name: string; role: string; detail?: string | null; onSignOut: () => void; prefs: Prefs }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const { pathname } = useLocation()
  useEffect(() => setOpen(false), [pathname])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus() } }
    const onClick = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onClick) }
  }, [open])
  return (
    <div className="menu" ref={box}>
      <button type="button" ref={btn} className="menu-btn user-block" aria-expanded={open} aria-controls="profile-menu" onClick={() => setOpen(!open)}>
        <span className="avatar" aria-hidden="true">{initials(name)}</span>
        <span className="user-text">
          <span className="user-name">{name}</span>
          <span className="user-role">{ROLE_LABEL[role] ?? role}{detail ? ` · ${detail}` : ''}</span>
        </span>
        <span className="sr-only">Account menu: settings and sign out</span>
        <ChevronUp className="icon-inline user-chev" aria-hidden="true" />
      </button>
      {open && (
        <ul className="menu-list" id="profile-menu">
          <li className="menu-head"><strong>{name}</strong><div className="who-role">{ROLE_LABEL[role] ?? role}</div></li>
          <li><Link className="menu-item" to="/account"><UserCircle className="icon" aria-hidden="true" />My account</Link></li>
          <li className="menu-section">
            <span className="menu-section-label" id="theme-label">Theme</span>
            <div className="segmented" role="group" aria-labelledby="theme-label">
              {([['system', 'Auto', Monitor], ['light', 'Light', Sun], ['dark', 'Dark', Moon]] as const).map(([t, label, Icon]) => (
                <button key={t} type="button" aria-pressed={prefs.theme === t} onClick={() => prefs.setTheme(t)}><Icon className="icon-inline" aria-hidden="true" />{label}</button>
              ))}
            </div>
          </li>
          <li className="menu-section">
            <span className="menu-section-label" id="display-label">Display</span>
            <div className="row" role="group" aria-labelledby="display-label">
              <button type="button" className="text-btn" onClick={() => prefs.setContrast(!prefs.contrast)} aria-pressed={prefs.contrast}>High contrast</button>
              <div className="segmented" role="group" aria-label={'Text size ' + Math.round(prefs.scale * 100) + '%'}>
                <button type="button" onClick={() => prefs.setScale(Math.max(0.8, Math.round((prefs.scale - 0.1) * 10) / 10))} aria-label="Smaller text" title="Smaller text"><Minus className="icon-inline" aria-hidden="true" /></button>
                <span className="segmented-value" aria-hidden="true">{Math.round(prefs.scale * 100)}%</span>
                <button type="button" onClick={() => prefs.setScale(Math.min(1.5, Math.round((prefs.scale + 0.1) * 10) / 10))} aria-label="Larger text" title="Larger text"><Plus className="icon-inline" aria-hidden="true" /></button>
              </div>
            </div>
          </li>
          <li className="menu-divider" role="separator" />
          <li><button type="button" className="menu-item" onClick={onSignOut}><LogOut className="icon" aria-hidden="true" />Sign out</button></li>
        </ul>
      )}
    </div>
  )
}

function Shell() {
  const { user, ready, logout } = useAuth()
  const prefs = useDisplayPrefs()
  const nav = useNavOpen()
  const { pathname } = useLocation()
  const counts = useNavCounts(user?.role ?? '', pathname)
  const { close } = nav
  const sidebarRef = useRef<HTMLElement>(null)
  const openerRef = useRef<HTMLButtonElement>(null)
  // On phones the open menu is a modal drawer: the page behind is inert, focus moves in, Escape closes it.
  const overlay = nav.open && !isWide()
  useEffect(() => {
    if (!isWide()) close()
    // Page content fades up on route change (brief §2 motion). The class is re-applied rather than the page
    // re-mounted, so no page state is lost; CSS turns it off under prefers-reduced-motion.
    const main = document.getElementById('main')
    if (main) {
      main.classList.remove('route-enter')
      void main.offsetWidth
      main.classList.add('route-enter')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname])
  function closeOverlay() {
    close()
    requestAnimationFrame(() => openerRef.current?.focus())
  }
  useEffect(() => {
    if (!overlay) return
    sidebarRef.current?.querySelector<HTMLElement>('nav a, button')?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeOverlay() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlay])
  if (!ready) return <main className="auth-page"><Loading /></main>
  if (!user) return <Navigate to="/login" replace />
  const mine = routes.filter((r) => r.roles.includes(user.role))
  const layout = NAV_LAYOUT[user.role]
  const navRoutes = mine.filter((r) => r.nav).map((r) => (layout?.[r.path] ? { ...r, group: layout[r.path][0], nav: layout[r.path][1] } : r))
  if (layout) {
    const order = Object.keys(layout)
    navRoutes.sort((a, b) => (order.indexOf(a.path) + 1 || 99) - (order.indexOf(b.path) + 1 || 99))
  }
  const groups = [...new Set(navRoutes.map((r) => r.group ?? ''))]
  const toggleLabel = nav.open ? 'Hide menu' : 'Show menu'
  // Section title for the top bar: the deepest navigation entry that contains the current page.
  const sectionRoute = navRoutes
    .filter((r) => (r.path === '/' ? pathname === '/' : pathname === r.path || pathname.startsWith(r.path + '/')))
    .sort((a, b) => b.path.length - a.path.length)[0]
  const section = sectionRoute?.nav
  const SectionIcon = sectionRoute ? NAV_ICON[sectionRoute.path] : undefined
  const student = user.role === 'student'
  return (
    <div className={`app${nav.open ? '' : ' nav-collapsed'}${student ? ' has-bottom-nav' : ''}`}>
      <a href="#main" className="skip-link">Skip to content</a>
      <aside className="sidebar" id="sidebar" ref={sidebarRef} aria-label="Menu">
        <div className="sidebar-head">
          <Brand sub />
          <button type="button" className="icon-btn" onClick={overlay ? closeOverlay : nav.toggle} aria-expanded={nav.open} aria-controls="sidebar" aria-label={toggleLabel} title={toggleLabel}>
            <PanelLeft className="icon" aria-hidden="true" />
          </button>
        </div>
        <nav aria-label="Main">
          {groups.map((g) => (
            <div key={g}>
              <div className="group">{g}</div>
              {navRoutes.filter((r) => (r.group ?? '') === g).map((r) => {
                const Icon = NAV_ICON[r.path] ?? FileText
                const n = counts[r.path] ?? 0
                return (
                  <NavLink key={r.path} to={r.path} end={r.path === '/'} title={nav.open ? undefined : r.nav}>
                    <Icon className="icon" aria-hidden="true" /><span className="nav-label">{r.nav}</span>
                    {n > 0 && <span className="nav-badge" aria-label={`${n} new`}>{n > 99 ? '99+' : n}</span>}
                  </NavLink>
                )
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-user">
          <ProfileMenu name={user.full_name} role={user.role} detail={user.student_number} onSignOut={logout} prefs={prefs} />
        </div>
      </aside>
      {nav.open && <button type="button" className="scrim" aria-label="Close menu" tabIndex={-1} onClick={closeOverlay} />}
      <div className="canvas" inert={overlay || undefined}>
        <header className="topbar">
          <button type="button" ref={openerRef} className="icon-btn" onClick={nav.toggle} aria-expanded={nav.open} aria-controls="sidebar" aria-label={toggleLabel} title={toggleLabel}>
            <PanelLeft className="icon" aria-hidden="true" />
          </button>
          <Brand />
          {section && <p className="topbar-title">{SectionIcon && <SectionIcon className="icon" aria-hidden="true" />}{section}</p>}
          <span className="topbar-spacer" />
          <Link to="/notifications" className="icon-btn notif-link" aria-label="Notifications" title="Notifications"><Bell className="icon" aria-hidden="true" /></Link>
        </header>
        {user.role === 'admin' && <UploadBanner />}
        <main id="main" tabIndex={-1}>
          <Routes>
            {mine.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
            <Route path="*" element={<><h1>Page not found</h1><p>This page does not exist or is not available for your role. <NavLink to="/">Go to the home page</NavLink></p></>} />
          </Routes>
        </main>
      </div>
      {student && (
        <nav className="bottom-nav" aria-label="Quick links" inert={overlay || undefined}>
          {STUDENT_BOTTOM.map((b) => {
            const Icon = NAV_ICON[b.to] ?? Home
            return <NavLink key={b.to} to={b.to} end={b.to === '/'}><Icon className="icon" aria-hidden="true" />{b.label}</NavLink>
          })}
        </nav>
      )}
      <ToastHost aboveBottomNav={student} />
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <RouteAnnouncer />
      <DialogHost />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/dev/outbox" element={<DevOutboxPage />} />
        {publicRoutes.map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
        <Route path="/*" element={<Shell />} />
      </Routes>
    </AuthProvider>
  )
}
