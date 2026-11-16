/** "Your instructor": who teaches the student's section and how to reach them. */
import { Clock, Mail, MapPin } from 'lucide-react'
import { api } from '../../api/client'
import { Card, Skeleton } from '../../components/kit/Kit'
import { useLoad } from '../../components/ui'

type T = { name: string; email: string; role: string; since: string; office_hours: string | null; office_location: string | null; about: string | null }
const initials = (n: string) => n.split(/\s+/).filter((w) => !/^(dr|mr|ms|mrs|prof)\.?$/i.test(w)).map((w) => w[0]).slice(0, 2).join('').toUpperCase()

export function InstructorCard({ courseId, className = '' }: { courseId: string; className?: string }) {
  const d = useLoad(() => api<{ section: { code: string; term: string | null } | null; instructors: T[] }>(`/learn/${courseId}/instructors`), [courseId])
  const sec = d.data?.section
  return (
    <Card className={className} title={(d.data?.instructors.length ?? 0) > 1 ? 'Your instructors' : 'Your instructor'}
      subtitle={sec ? `${sec.term ? `${sec.term} · ` : ''}Section ${sec.code}` : undefined}>
      {d.loading && !d.data ? <Skeleton lines={3} label="Loading your instructor" /> : !d.data?.instructors.length ? (
        <p className="kempty">No instructor is assigned to your section yet. Your department will announce one soon.</p>
      ) : (
        <ul className="teacher-list">
          {d.data.instructors.map((t) => (
            <li key={t.email}>
              <span className="avatar-md" aria-hidden="true">{initials(t.name)}</span>
              <div className="min-w-0">
                <p className="teacher-name">{t.name}{t.role === 'assistant' && <span className="small muted"> · teaching assistant</span>}</p>
                <p className="teacher-line"><Mail aria-hidden="true" /><a href={`mailto:${t.email}`}>{t.email}</a></p>
                <p className="teacher-line"><Clock aria-hidden="true" />{t.office_hours ? <>Office hours: {t.office_hours}</> : <span className="muted">Office hours not posted yet</span>}</p>
                {t.office_location && <p className="teacher-line"><MapPin aria-hidden="true" />{t.office_location}</p>}
                {t.about && <p className="teacher-about">{t.about}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
