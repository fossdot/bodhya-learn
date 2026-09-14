import sys
from playwright.sync_api import sync_playwright

GAME = "http://localhost:8002/assets/hikmat/game.html"
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_context(viewport={"width":420,"height":860}).new_page()
    page.on("console", lambda m: print("  [err]", m.text[:200]) if m.type=="error" else None)
    page.goto(GAME)
    page.wait_for_function("typeof COURSES !== 'undefined' && COURSES.length > 5", timeout=20000)
    page.wait_for_function("typeof LEVEL_BANK !== 'undefined' && LEVEL_BANK.bank && LEVEL_BANK.bank.length > 100", timeout=20000)
    page.wait_for_timeout(1200)

    # ---- F1: an inactive Test Level must NOT freeze the ladder ----
    r = page.evaluate("""() => {
      const save_ = JSON.stringify(LEVEL_BANK.levels);
      const before = studentLevel();
      LEVEL_BANK.levels = LEVEL_BANK.levels.map(l => l.level === 1 ? {...l, active:false} : l);
      dropLevelCache();
      const after = studentLevel(), cleared = levelCleared(1), due = levelTestDue(1);
      // is anything above still walled?
      let walled = 0;
      COURSES.forEach(c => { if(!c.published) return; c.lessons.forEach(ls => { if(lessonLock(c, ls)) walled++; }); });
      LEVEL_BANK.levels = JSON.parse(save_); dropLevelCache();
      return {before, after, cleared, due, walled};
    }""")
    print("  F1:", r)
    check(r["before"] == 1, "baseline: fresh learner is at L1")
    check(r["cleared"] is True and r["after"] > 1, "inactive L1 counts as cleared and the ladder walks past it")
    check(r["due"] is False, "an inactive level's test is still never 'due'")

    # ---- F6: no bank + no quiz items anywhere => nothing may lock forever ----
    r = page.evaluate("""() => {
      const bank_ = LEVEL_BANK.bank, quizzes = [];
      COURSES.forEach(c => c.lessons.forEach(ls => { quizzes.push([ls, ls.quiz]); ls.quiz = []; }));
      LEVEL_BANK.bank = []; dropLevelCache();
      const lvl = studentLevel();
      let locked = 0, total = 0;
      COURSES.forEach(c => { if(!c.published) return; c.lessons.forEach(ls => { total++; if(lessonLock(c, ls)) locked++; }); });
      const reach1 = levelTestReachable(1);
      LEVEL_BANK.bank = bank_; quizzes.forEach(([ls,q]) => ls.quiz = q); dropLevelCache();
      return {lvl, locked, total, reach1};
    }""")
    print("  F6:", r)
    check(r["reach1"] is False, "with no bank and no quiz items, L1's test is unreachable")
    check(r["lvl"] == 6 and r["locked"] == 0, "standalone/no-bank build locks NOTHING (was: everything above L1 forever)")

    # ---- F4: setStudent must drop the per-level cache (two identities, one phone) ----
    r = page.evaluate("""() => {
      const c = COURSES.find(c => c.published && c.lessons.length);
      const ls = c.lessons[0], n = lessonLevel(c, ls);
      setStudent({id:"girlA", name:"A", local:true});
      levelsFor(ls).forEach(lv => setStars(c.key, ls.key, lv.id, 3));
      save();
      const aStars = levelStars(n), aKeys = Object.keys(_lvCache).length;
      setStudent({id:"girlB", name:"B", local:true});
      const bDropped = Object.keys(_lvCache).length, bStars = levelStars(n);
      setStudent({id:"girlA", name:"A", local:true});
      const aAgain = levelStars(n);
      setStudent(null);
      try { localStorage.removeItem("hikmat_state_girlA"); localStorage.removeItem("hikmat_state_girlB"); } catch(e){}
      return {aStars, aKeys, bDropped, bStars, aAgain};
    }""")
    print("  F4:", r)
    check(r["aStars"] > 0, "learner A earned stars at her level")
    check(r["aKeys"] > 0 and r["bDropped"] == 0, "switching student empties _lvCache")
    check(r["bStars"] == 0, "learner B does NOT inherit A's star rollups (the leak is closed)")
    check(r["aAgain"] == r["aStars"], "switching back gives A her own totals again")

    # ---- F5: guest -> real identity keeps level passes and seen-question lists ----
    r = page.evaluate("""() => {
      state.levelTests = {1:{passed:true, bestPct:82, attempts:1}};
      state.testSeen  = {L1:["q-a","q-b"]};
      save();
      const snap = snapshotGuestProgress();
      const had = !!(snap && snap.levelTests && snap.testSeen);
      // simulate the post-signup identity swap: a fresh state for a new id
      state = {progress:{}, coins:0, levelTests:{}, testSeen:{}};
      migrateGuestProgress(snap);
      return {had, passed: levelPassed(1),
              bestPct: (state.levelTests[1]||{}).bestPct,
              seen: ((state.testSeen||{}).L1||[]).slice().sort()};
    }""")
    print("  F5:", r)
    check(r["had"] is True, "snapshotGuestProgress() now captures levelTests + testSeen")
    check(r["passed"] is True and r["bestPct"] == 82, "a pass earned as a guest survives signing up")
    check(r["seen"] == ["q-a","q-b"], "the seen-question list survives too (no repeat paper)")

    # ---- F2/F7: a live test owns navigation ----
    r = page.evaluate("""() => {
      const prev = TEST;
      TEST = {done:false, level:1};
      const guard = testOwnsNav();
      document.getElementById("levelPill").click();
      const sheet = !!document.getElementById("lvlWrap");
      document.getElementById("menuBtn").click();
      const menu = !!document.querySelector(".sheetwrap");
      const bw = document.getElementById("brandWrap");
      let navigated = false;
      const mark = "__probe__" + Math.floor(performance.now());
      root.setAttribute("data-probe", mark);
      bw.click();
      navigated = root.getAttribute("data-probe") !== mark;   // renderHome would wipe the attr
      TEST = prev;
      return {guard, sheet, menu, navigated, off: testOwnsNav()};
    }""")
    print("  F2/F7:", r)
    check(r["guard"] is True and r["off"] is False, "testOwnsNav() is true only while a test is live")
    check(r["sheet"] is False, "the L-pill cannot open the level sheet mid-test")
    check(r["menu"] is False, "the menu cannot open mid-test")
    check(r["navigated"] is False, "the wordmark cannot navigate Home mid-test")

    # ---- F8: startTest is not re-entrant ----
    r = page.evaluate("""() => {
      const prev = TEST;
      const sentinel = {done:false, level:1, _sentinel:true};
      TEST = sentinel;
      startTest(1, () => {});
      const same = TEST === sentinel;
      TEST = prev;
      return {same};
    }""")
    print("  F8:", r)
    check(r["same"] is True, "startTest() refuses to start a second paper over a live one")

    # ---- F11: locked level rows keep AA contrast ----
    r = page.evaluate("""() => {
      const d = document.createElement("div");
      d.className = "levellist";
      d.innerHTML = '<div class="levelrow locked"><span class="lvbadge">L5</span>' +
                    '<span class="lvmain"><b>Level 5</b><small>Pass Level 4 first</small></span>' +
                    '<span class="lvright">🔒</span></div>';
      document.body.appendChild(d);
      const row = d.querySelector(".levelrow.locked");
      const op = getComputedStyle(row).opacity;
      const small = getComputedStyle(row.querySelector("small")).color;
      d.remove();
      return {op, small};
    }""")
    print("  F11:", r)
    check(abs(float(r["op"]) - 1.0) < 1e-6, "locked rows are no longer faded as a whole (opacity 1)")

    b.close()

print("\nFAILS", len(fails), fails)
sys.exit(1 if fails else 0)
