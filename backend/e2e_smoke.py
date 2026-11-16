"""End-to-end smoke test against the RUNNING app (start both servers, run `python -m app.cli seed`, then `python e2e_smoke.py`)."""
import json
import ssl
import time
import urllib.request

BASE = "https://localhost:5173"
CTX = ssl._create_unverified_context()
PW = "Demo2026pass"
fails = []


def call(method, path, token=None, body=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if not cond else ""))
    if not cond:
        fails.append(name)


for _ in range(60):
    try:
        if call("GET", "/api/health")[0] == 200:
            break
    except Exception:
        time.sleep(1)

st, page = call("GET", "/", raw=True)
check("frontend serves the SPA", st == 200 and b'id="root"' in page)


def login(email):
    st, b = call("POST", "/api/auth/login", body={"email": email, "password": PW})
    check(f"login {email}", st == 200, st)
    return b["access_token"]


A, S, AY, BI, EX = (login(e) for e in ["admin@university.edu", "sara.instructor@university.edu", "ayesha.student@university.edu",
                                       "bilal.student@university.edu", "exams@university.edu"])
st, v = call("GET", "/api/curriculum/versions", A)
check("US-1.1 curriculum version present", st == 200 and v[0]["status"] == "approved", v)
st, c = call("GET", "/api/outcomes/courses", A)
codes = {x["code"]: x for x in c}
check("US-1.2/1.3 courses approved/active", codes["CS101"]["status"] == "active" and codes["MATH110"]["status"] == "approved")
st, r = call("POST", f"/api/courses/{codes['MATH110']['id']}/activate", A)
check("US-1.3 activation blocked when mappings incomplete", st == 409, st)
cid = codes["CS101"]["id"]
st, e = call("GET", f"/api/criteria/effective/course/{codes['CS201']['id']}", A)
check("US-1.4 CS201 override 65%", e["clos"][0]["threshold"] == 65)
st, ai = call("GET", "/api/ai/calls", A)
check("US-13.2 AI calls traced", st == 200 and ai["total"] > 0)
st, au = call("GET", "/api/audit/verify", EX)
check("US-3.1 audit chain intact (exam staff)", st == 200 and au["valid"], au)
st, _ = call("GET", "/api/users", S)
check("US-11.1 instructor denied user admin", st == 403)
st, cat = call("GET", "/api/enrollment/catalog", BI)
cs201 = next(x for x in cat if x["code"] == "CS201")
check("US-2.4 prerequisite shown unmet", not cs201["prerequisites"][0]["met"])
st, p = call("GET", f"/api/paths/me/{cid}", AY)
check("US-4.3 Ayesha has a path covering all CLOs", st == 200 and p["covers_all_clos"])
st, units = call("GET", f"/api/learn/{cid}/units", AY)
check("US-5.1 units published", all(u["available"] for u in units))
st, u = call("GET", f"/api/learn/{cid}/units/{units[0]['id']}", AY)
check("US-5.1 lesson body returned", u["lesson"] and len(u["lesson"]["body_md"]) > 100)
st, ans = call("POST", "/api/ai/ask", AY, {"course_id": cid, "question": "What is a base case?"})
check("US-13.2 grounded tutor answer with citations", st == 200 and ans["citations"], ans)
clo = next(s for s in p["steps"] if s["step_type"] in ("practice", "review"))
st, q = call("POST", "/api/quiz/start", AY, {"course_id": cid, "clo_id": clo["clo_id"]})
check("US-6.1 quiz starts", st == 200, q)
st, h = call("POST", f"/api/quiz/attempts/{q['id']}/hint", AY, {"item_id": q["questions"][0]["id"]})
check("US-6.2 hint returned", st == 200 and h["level"] == 1, h)
for qq in q["questions"]:
    call("POST", f"/api/quiz/attempts/{q['id']}/answer", AY, {"item_id": qq["id"], "choice": 0})
st, fin = call("POST", f"/api/quiz/attempts/{q['id']}/finish", AY)
check("US-6.1/4.5 quiz finished, mastery + adaptation", st == 200 and fin["mastery"] and fin["adaptation"], fin)
st, ov = call("GET", "/api/console/overview", S)
check("US-13.1 console overview", st == 200 and ov[0]["enrolled"] == 3)
st, esc = call("GET", f"/api/escalations?course_id={cid}", S)
check("US-6.4 Bilal escalated", st == 200 and esc and esc[0]["student"]["name"] == "Bilal Ahmed")
st, ver = call("GET", f"/api/paths/course/{cid}/student/{esc[0]['student']['id']}/versions", S)
check("US-4.4 path version history", st == 200 and len(ver) >= 2)
st, pr = call("GET", f"/api/progress/me/{cid}/download?format=pdf", AY, raw=True)
check("US-10.1 PDF progress download", st == 200 and pr[:4] == b"%PDF")
st, ap = call("GET", "/api/appeals", S)
check("NB-4 pending appeal visible to instructor", st == 200 and ap and ap[0]["status"] == "submitted")
st, src = call("GET", f"/api/sources?course_id={cid}", S)
check("NB-2 approved source", src[0]["status"] == "approved")
st, secs = call("GET", "/api/offerings/sections", A)
check("NB-1 sections with instructors", all(s["instructors"] for s in secs))
st, pol = call("GET", "/api/mastery/policy", S)
check("NB-3 mastery policy", pol["rule_version"] == "best-of-last-3/v1")
print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
