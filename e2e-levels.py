"""End-to-end walkthrough of the L1-L5 level ladder, against a running site.

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
    bench serve --port 8002
    /tmp/pw/bin/python e2e-levels.py

Signs a learner up, earns her the star quota, sits the whole 20-question paper (letting one
question time out), and checks the promotion and the single submit_test POST that follows.

It also pins the rule that replaced the old ladder wall: NOTHING LOCKS. The test still comes due,
still appears on the trail, still promotes her — but no lesson anywhere is ever shut behind it.
Lives in the repo because two copies of it have now been eaten by /tmp cleanup.
"""
import json, re, sys
from urllib.parse import parse_qs
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8002"
GAME = BASE + "/assets/hikmat/game.html"
NAME = "Verify Level Girl"
posts = []
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 420, "height": 860})
    page = ctx.new_page()
    page.on("request", lambda r: posts.append((r.url, r.post_data)) if r.method == "POST" and "hikmat.api." in r.url else None)
    page.on("console", lambda m: print("  [console]", m.type, m.text[:160]) if m.type in ("error",) else None)
    page.goto(GAME)
    page.wait_for_function("typeof COURSES !== 'undefined' && COURSES.length > 5", timeout=15000)
    page.wait_for_timeout(2500)

    # ---------- 1. SIGN-UP: class chips include 11-12 / Bachelor's / Master's ----------
    page.evaluate("renderSignup()")
    page.wait_for_selector("#bandpick .bandchip")
    chips = page.locator("#bandpick .bandchip").all_inner_texts()
    print("class chips:", chips)
    check(len(chips) == 6, "signup shows 6 class options")
    check(any("11" in c for c in chips) and any("Bachelor" in c for c in chips) and any("Master" in c for c in chips), "11-12 / Bachelor's / Master's present")
    page.fill("#suName", NAME)
    page.locator("#bandpick .bandchip", has_text="Bachelor").click()
    page.locator("#genderpick .bandchip").nth(0).click()
    page.fill("#suPin", "4321")
    if page.locator("#suConsent").count(): page.check("#suConsent")
    page.wait_for_function("!document.querySelector('#suGo').disabled")
    page.click("#suGo")
    page.wait_for_function("typeof currentStudent !== 'undefined' && currentStudent && !currentStudent.local", timeout=15000)
    stu = page.evaluate("({id: currentStudent.id, band: currentStudent.band})")
    print("student:", stu)
    check(stu["band"] == "ug", "signed up with band=ug")
    page.wait_for_timeout(800)
    page.evaluate("renderHome()"); page.wait_for_timeout(300)
    home_txt = page.locator("#homeSections").inner_text()
    check("9" in home_txt and ("your class" in home_txt.lower() or "तुम्हारी कक्षा" in home_txt), "Home maps ug -> Class 9-10 card under 'Your class'")

    # ---------- 2. Fresh learner: L1, no test due ----------
    pill = page.locator("#levelPill").inner_text()
    check(pill.strip() == "L1", f"level pill starts at L1 (got {pill!r})")
    check(page.evaluate("levelTestDue(1)") is False, "no test due at zero stars")
    page.evaluate("renderTrack(COURSES.find(c => c.key === 'eng-foundation'))"); page.wait_for_timeout(300)
    locked = page.locator("#path .step.locked").count(); total = page.locator("#path .step").count()
    print("eng-foundation steps:", total, "locked:", locked)
    check(total == 10 and locked == 0, "nothing is locked — the whole track is open at level 1")
    check("Opens at Level" not in page.locator("#path").inner_text(), "no 'Opens at Level' wall on the trail")

    # ---------- 3. Earn 100+ stars on L1 lessons -> test becomes due ----------
    page.evaluate("""() => {
      let stars = 0;
      const order = [COURSES.find(c => c.key === 'eng-foundation')].concat(COURSES.filter(c => c.key !== 'eng-foundation'));
      order.forEach(c => { if(!c || !c.published) return; c.lessons.forEach(ls => {
        if(lessonLevel(c, ls) !== 1 || stars >= 110) return;
        if(c.key !== 'eng-foundation' && !(ls.quiz && ls.quiz.length)) return;
        levelsFor(ls).forEach(lv => { setStars(c.key, ls.key, lv.id, 3); stars += 3; });
      }); });
      save(); refreshTop();
    }""")
    lv1 = page.evaluate("[levelStars(1), levelTestDue(1), levelPool(1).length, studentLevel()]")
    print("levelStars(1), due, pool, studentLevel:", lv1)
    check(lv1[0] >= 100 and lv1[1] is True, "level 1 test is due after 100 stars")
    check(lv1[2] >= 20, "pool has at least 20 questions (bank + lesson quiz items)")
    pill = page.locator("#levelPill").inner_text()
    check("🏆" in pill and page.locator("#levelPill.due").count() == 1, f"pill flags the due test ({pill!r})")
    page.evaluate("renderTrack(COURSES.find(c => c.key === 'eng-foundation'))"); page.wait_for_timeout(300)
    ptxt = page.locator("#path").inner_text()
    check("Level 1 test" in ptxt and "Ready" in ptxt, "trail shows the Level 1 test node")
    other = page.evaluate("""() => { for(const c of COURSES){ if(!c.published) continue; for(const ls of c.lessons){ if(lessonLevel(c, ls)===1 && lessonEarned(c.key, ls.key)===0) return [c.key, ls.key, JSON.stringify(lessonLock(c, ls))]; } } return null; }""")
    print("unplayed L1 lesson lock:", other)
    check(other and other[2] == "null", "an unplayed lesson is NOT paused by a due test")
    # the strong form: with a test due and the quota met, NOTHING anywhere may be shut
    shut = page.evaluate("""() => { let n = 0;
      COURSES.forEach(c => { if(!c.published) return; c.lessons.forEach(ls => { if(lessonLock(c, ls)) n++; }); });
      return n; }""")
    print("locked lessons across all 283:", shut)
    check(shut == 0, "no lesson anywhere in the curriculum is locked")
    page.click("#levelPill", force=True); page.wait_for_selector("#lvlWrap .levelrow")
    check(page.locator("#lvlWrap .levelrow").count() == 5 and page.locator("#lvGo").count() == 1, "level sheet lists 5 levels + a Take-the-test button")
    page.click("#lvlWrap #dClose")

    # ---------- 4. Take the test: intro -> questions (one times out) -> pass ----------
    page.locator("#path .node", has_text="🏆").click()
    page.wait_for_selector("#tstart")
    itxt = page.locator("#root").inner_text()
    check("20 questions" in itxt and "75%" in itxt, "intro states 20 questions and the 75% pass mark")
    page.click("#tstart")
    page.wait_for_selector("#ttimer", timeout=5000)
    n = page.evaluate("TEST.paper.qs.length")
    print("paper size:", n, "first timer:", page.locator("#ttimer").inner_text())
    check(n == 20, "paper has 20 questions")
    check(re.match(r"⏱ \d+", page.locator("#ttimer").inner_text()) is not None, "per-question countdown shown")
    timed_out = False
    for i in range(n):
        page.wait_for_function(f"TEST && TEST.idx === {i} && !TEST._answered", timeout=5000)
        if i == 2:
            page.evaluate("TEST.qDeadline = Date.now() + 1200")
            page.wait_for_function("document.querySelector('#fb') && document.querySelector('#fb').textContent.indexOf('Time') >= 0", timeout=5000)
            timed_out = True
            continue
        ans = page.evaluate("TEST.paper.qs[TEST.idx].answer")
        page.locator(".quizopt", has_text=re.compile("^" + re.escape(ans) + "$")).first.click()
    check(timed_out, "a question timed out and moved on")
    page.wait_for_selector(".result", timeout=8000)
    rtxt = page.locator(".result").inner_text()
    print("result:", rtxt.replace("\n", " | ")[:220])
    check("95%" in rtxt and ("Level 2" in rtxt), "passed with 95% and promoted to Level 2")
    check(page.evaluate("studentLevel()") == 2, "studentLevel() is now 2")
    check("L2" in page.locator("#levelPill").inner_text(), "pill shows L2")
    page.click("#tdone"); page.wait_for_selector("#path .step")
    locked2 = page.locator("#path .step.locked").count()
    ptxt = page.locator("#path").inner_text()
    check(locked2 == 0 and "Opens at Level" not in ptxt, "still nothing locked after passing")
    page.wait_for_timeout(1500)

    # ---------- 5. Network evidence ----------
    sub = [(u, d) for u, d in posts if "submit_test" in u]
    check(len(sub) == 1, f"exactly one submit_test POST ({len(sub)})")
    if sub:
        q = parse_qs(sub[0][1])
        print("submit_test body keys:", sorted(q.keys()))
        check(q.get("level") == ["1"] and q.get("status") == ["completed"], "POST carries level=1, status=completed")
        answers = json.loads(q.get("answers", ["{}"])[0]); paper = json.loads(q.get("paper", ["[]"])[0])
        check(len(paper) == 20 and len(answers) == 20 and sum(1 for v in answers.values() if v == "") == 1, "paper=20 ids, answers=20 (one blank for the timed-out question)")
        bank_ids = [i for i in paper if not i.startswith("quiz:")]
        print("bank ids on paper:", len(bank_ids), "quiz fallbacks:", 20 - len(bank_ids))
    b.close()

print("\nSTUDENT_ID", stu["id"])
print("FAILS", len(fails), fails)
sys.exit(1 if fails else 0)
