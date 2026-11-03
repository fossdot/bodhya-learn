"""Saves go through every update under test: v7 to the new build and back.

    python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
    /tmp/pw/bin/python e2e-progress-safety.py                       # everything, web and native
    /tmp/pw/bin/python e2e-progress-safety.py --mode web --only v7-midway
    /tmp/pw/bin/python e2e-progress-safety.py --self-test           # prove the checks still bite
    /tmp/pw/bin/python e2e-progress-safety.py --make-fixtures       # rebuild tests/saves/*.json from play-v7

Why this exists. A girl's stars, gems and level passes live in one localStorage key on her own
phone and nowhere else. A Play update replaces the code that reads that key, and it gets no second
chance at the data: a build that misreads it, or writes a smaller copy over it, has lost her work
for good. So before a build ships, the saves it will meet are replayed through it. Each one is
seeded into a fresh browser, opened in the build she has now (git ref play-v7, the game.html the
v7 AAB was built from) and then in the new build on the same origin, which is how an update lands on her
phone. She relaunches three times. Last, the old build opens what the new one left, because a
release that has to be pulled must hand her back the same progress.

The checks (docs/plans/2026-11-03-step-0-progress-safety.md, Task 1), each named with the fixture:
  I1  no seeded hikmat_state_* key goes missing; no star, coins, bestPct or attempts drops; no
      passed test turns false
  I2  her star total and her level never drop
  I3  every lesson she has played that the old build kept open is still open
  I4  her settings are unchanged: language, theme, sound, Hindi form, friend, prefsTouched
  I5  three more launches change nothing at all
  R   the old build reads what the new one left: the same total and level, every played lesson
      open, and the full curriculum
and every launch must be free of page errors and console errors.

Nothing here needs a server or the network. A static server serves hikmat/public/ under
/assets/hikmat/, which is where Frappe serves it and where the Android bundle copies it, so the
game's absolute asset paths resolve unchanged. The old build is answered from git, and every request
to any other host is refused, so a run can never reach learn.bodhya.net. --mode native also
defines window.Capacitor with fakes of Preferences, Filesystem and Share. Their shapes and error
messages follow the real @capacitor/preferences 8.0.1, filesystem 8.1.5 and share 8.0.3, and they
are backed by a Python store (Phone.NATIVE) that outlives reloads and localStorage.clear(), the
way SharedPreferences outlives a WebView that comes back empty.

Synthetic fixtures live in tests/saves/ and are committed. Real tester saves go in
tests/saves/private/, which is gitignored, and every file there runs too: see tests/saves/README.md.

Adding a test: decorate `def t_name(env, report)` with @test("name"). `env.phone()` gives a fresh
phone (its own storage, and its own NATIVE store in native mode); `phone.seed(fixture)`,
`phone.launch("old"|"new")`, `phone.snap()` and `phone.page` do the rest. Report with
`report.check(cond, msg)`, and start msg with the tag `[<mode>] <name>`.
"""
import argparse
import ast
import functools
import http.server
import json
import math
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

ROOT = Path(__file__).resolve().parent              # apps/hikmat
PUBLIC = ROOT / "hikmat" / "public"
SAVES = ROOT / "tests" / "saves"
PRIVATE = SAVES / "private"
SETUP_DATA = ROOT / "hikmat" / "setup_data.py"

PREFIX = "/assets/hikmat/"
NEW_PATH = PREFIX + "game.html"
OLD_PATH = PREFIX + "game-old.html"
SEED_PATH = PREFIX + "__seed__.html"
SEED_HTML = "<!doctype html><meta charset='utf-8'><title>seed</title>"
VIEWPORT = {"width": 360, "height": 640}
CACHE_KEYS = ("hikmat_courses_cache", "hikmat_structure_cache", "hikmat_testbank_cache")
APP_ID = "net.bodhya.learn"
# A static host answers 404 for the backend's whitelisted methods (the game itself says so in
# loadFromBackend), and Chromium logs each one as a console error. Those four lines are the
# expected shape of an offline phone, not a fault, so they are the only load failures ignored.
API_PATH = re.compile(r"^/api/method/hikmat\.api\.")

CURRICULUM = json.loads((PUBLIC / "curriculum.json").read_text(encoding="utf-8"))
CURRICULUM_LEN = len(CURRICULUM)
CURRICULUM_LESSONS = {(c.get("key"), ls.get("key")) for c in CURRICULUM for ls in (c.get("lessons") or [])}


def compact(obj):
    """JSON the way the game's JSON.stringify writes it: no spaces, non-ASCII kept as is."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _dict(x):
    return x if isinstance(x, dict) else {}


def _num(x):
    """A stored count as a number, or None when it is not one. A hand-edited or damaged save can
    hold "3" or null where a star should be; those cannot be compared, so they are skipped rather
    than counted as a drop."""
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, (int, float)) and math.isfinite(x):
        return x
    return None


# ---------------------------------------------------------------- in-page probes --------------

# SNAP: what she can see and what is stored, in one object. Read through the game's own
# functions, so every build is judged by its own rules for totals, levels and locks.
SNAP_JS = r"""() => {
  const stateKeys = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || k.indexOf("hikmat_state_") !== 0) continue;
    const raw = localStorage.getItem(k);
    let v;
    try { v = JSON.parse(raw); } catch (e) { v = { __unparseable__: String(raw).slice(0, 120) }; }
    stateKeys[k] = v;
  }
  const playedOpen = [];
  const p = (state && state.progress) || {};
  for (const ck in p) {
    const course = COURSES.find(c => c.key === ck);
    if (!course) continue;
    for (const lk in (p[ck] || {})) {
      if (!(lessonEarned(ck, lk) > 0)) continue;
      const ls = (course.lessons || []).find(l => l.key === lk);
      if (ls && lessonLock(course, ls) === null) playedOpen.push([ck, lk]);
    }
  }
  playedOpen.sort((a, b) => (a[0] + "\u0000" + a[1]) < (b[0] + "\u0000" + b[1]) ? -1 : 1);
  const s = state || {};
  const v = x => (x === undefined ? null : x);
  return {
    stateKeys,
    total: totalStars(),
    level: studentLevel(),
    playedOpen,
    // prefsTouched is only ever read for its truth (applyServerDefaults), so absent and false
    // are the same setting to her; every other field is compared exactly as the game holds it.
    settings: { lang: v(s.lang), theme: v(s.theme), sound: v(s.sound), gender: v(s.gender),
                mascot: v(s.mascot), prefsTouched: !!s.prefsTouched },
    coursesLen: COURSES.length,
  };
}"""

# The boot promises a build may expose. v7 has neither, hence the typeof guards. A rejection is
# a bug in the build (nothing at boot may throw her progress away), so it is reported, not hidden.
BOOT_JS = r"""async () => {
  const out = {};
  if (typeof contentReady !== "undefined") {
    try { out.contentReady = await contentReady; } catch (e) { out.contentReadyError = String((e && e.stack) || e); }
  }
  if (typeof nativeRestore !== "undefined") {
    try { out.nativeRestore = await nativeRestore; } catch (e) { out.nativeRestoreError = String((e && e.stack) || e); }
  }
  return out;
}"""

# Fill localStorage to the brim, the way a phone holding both content caches plus years of play
# ends up. Big chunks first, then smaller ones, until not even one more key fits. A new key costs
# its name as well, so a few characters can still be left over; the last filler key is then
# topped up one character at a time. Without that, a save that changes a star from 2 to 3, or adds
# one short entry, could still squeeze into the leftover space and the phone would not really be full.
FILL_JS = r"""() => {
  let n = 0;
  for (const size of [1 << 20, 1 << 18, 1 << 16, 1 << 14, 1 << 12, 1 << 10, 256, 64, 16, 4, 1]) {
    const chunk = "z".repeat(size);
    for (;;) {
      try { localStorage.setItem("zz_filler_" + n, chunk); n++; } catch (e) { break; }
    }
  }
  if (n > 0) {
    const last = "zz_filler_" + (n - 1);
    for (const size of [256, 64, 16, 4, 1]) {
      const more = "z".repeat(size);
      for (;;) {
        try { localStorage.setItem(last, localStorage.getItem(last) + more); } catch (e) { break; }
      }
    }
  }
  let full = false;
  try { localStorage.setItem("zz_filler_probe", "z"); localStorage.removeItem("zz_filler_probe"); }
  catch (e) { full = true; }
  if (full && n > 0) {
    const last = "zz_filler_" + (n - 1), v = localStorage.getItem(last);
    try { localStorage.setItem(last, v + "z"); localStorage.setItem(last, v); full = false; } catch (e) {}
  }
  return { n, full };
}"""

# Everything --make-fixtures needs from the old build, read through its own functions, so every
# game id, lesson level and question id in a fixture is one that build really uses.
CATALOG_JS = r"""() => ({
  courses: COURSES.map(c => ({
    key: c.key, published: !!c.published, band: c.band || "", subject: c.subject || "",
    lessons: (c.lessons || []).map(ls => ({
      key: ls.key, level: lessonLevel(c, ls),
      games: levelsFor(ls).map(lv => [lv.id, lv.type]),
    })),
  })),
  bank: (LEVEL_BANK.bank || []).map(q => [q.id, q.t, q.l]),
  mascots: MASCOTS.map(m => [m.id, !!m.male]),
})"""

# The Capacitor bridge as the game sees it, defined before any page script runs. Every method
# crosses to Python through one binding, so the store behind it is outside the page entirely.
# Preferences.get is two crossings: the first logs the call and returns the switch get_delay_ms,
# the second reads the value. A write that arrives during the delay is therefore visible in the
# log as landing between a get's start and its result, which is the boot race this exists to catch.
NATIVE_JS = r"""(() => {
  if (window.Capacitor) return;
  const call = (op, args) => window.__bodhyaNative(op, args == null ? {} : args).then(r => {
    if (r && r.__error) throw new Error(r.__error);
    return r;
  });
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const Preferences = {
    configure: o => call("Preferences.configure", o).then(() => undefined),
    get: async o => {
      const b = await call("Preferences.get:begin", o);
      if (b.delay > 0) await wait(b.delay);
      const r = await call("Preferences.get", o);
      return { value: r.value };
    },
    set: o => call("Preferences.set", o).then(() => undefined),
    remove: o => call("Preferences.remove", o).then(() => undefined),
    clear: () => call("Preferences.clear").then(() => undefined),
    keys: () => call("Preferences.keys").then(r => ({ keys: r.keys })),
    migrate: () => Promise.resolve({ migrated: [], existing: [] }),
    removeOld: () => Promise.resolve(),
  };
  const Filesystem = {
    writeFile: o => call("Filesystem.writeFile", o).then(r => ({ uri: r.uri })),
    readFile: o => call("Filesystem.readFile", o).then(r => ({ data: r.data })),
    deleteFile: o => call("Filesystem.deleteFile", o).then(() => undefined),
    getUri: o => call("Filesystem.getUri", o).then(r => ({ uri: r.uri })),
  };
  const Share = {
    canShare: () => Promise.resolve({ value: true }),
    share: o => call("Share.share", o).then(r => ({ activityType: r.activityType })),
  };
  const Plugins = { Preferences, Filesystem, Share };
  window.Capacitor = {
    Plugins,
    getPlatform: () => "android",
    isNativePlatform: () => true,
    isPluginAvailable: name => Object.prototype.hasOwnProperty.call(Plugins, name),
  };
})();"""


# ---------------------------------------------------------------- the native side -------------

class Native:
    """The phone's side of the Capacitor bridge, one per phone (this is the plan's NATIVE store).

    It lives in Python, so it survives reloads, relaunches and localStorage.clear(), the way
    shared_prefs/CapacitorStorage.xml survives a WebView that comes back empty. The rules copy the
    Android plugins: a missing key reads as null, a set without a key is refused with "Must provide
    key", a non-string value removes the key (PluginCall.getString() gives null and putString(key,
    null) is a remove), and Share takes only file:// urls.

    Switches a test can flip between calls:
      drop_next_write  the next Preferences.set resolves but stores nothing: a lost native write
      get_delay_ms     every Preferences.get waits this long before it reads: a slow bridge at boot
    `log` holds every call in the order the bridge received it.
    """
    GROUP = "CapacitorStorage"

    def __init__(self):
        self.groups = {self.GROUP: {}}
        self.group = self.GROUP
        self.files = {}          # path -> {"path", "data", "directory", "encoding", "uri"}
        self.shares = []         # every Share.share() options object, in order
        self.log = []
        self.drop_next_write = False
        self.get_delay_ms = 0

    @property
    def prefs(self):
        """The active Preferences group, key -> string: what CapacitorStorage.xml holds."""
        return self.groups.setdefault(self.group, {})

    def _note(self, op, **kw):
        kw.update(op=op, i=len(self.log))
        self.log.append(kw)

    def writes_before_read(self, key):
        """Every write that could have changed `key` before the first read of it had resolved.
        Review Focus 1: a save during boot must never reach the native copy before boot has read
        it. A key that was never read at all returns every write to it."""
        out = []
        for e in self.log:
            if e["op"] == "clear":
                out.append(e)
            elif e.get("key") == key:
                if e["op"] == "get":
                    return out
                if e["op"] in ("set", "remove"):
                    out.append(e)
        return out

    def _uri(self, path, directory):
        d = directory.lower() if isinstance(directory, str) and directory else ""
        return "file:///" + (d + "/" if d else "") + path.lstrip("/")

    def call(self, op, args):
        a = args if isinstance(args, dict) else {}
        key = a.get("key")
        if op in ("Preferences.get:begin", "Preferences.get", "Preferences.set", "Preferences.remove") \
                and not isinstance(key, str):
            return {"__error": "Must provide key"}
        if op == "Preferences.configure":
            g = a.get("group")
            self.group = g if isinstance(g, str) and g else self.GROUP
            self._note("configure", group=self.group)
            return {}
        if op == "Preferences.get:begin":
            self._note("get:begin", key=key)
            return {"delay": max(0, int(self.get_delay_ms or 0))}
        if op == "Preferences.get":
            v = self.prefs.get(key)
            self._note("get", key=key, found=v is not None)
            return {"value": v}
        if op == "Preferences.set":
            v = a.get("value") if isinstance(a.get("value"), str) else None
            if self.drop_next_write:
                self.drop_next_write = False
                self._note("set", key=key, size=len(v or ""), dropped=True)
                return {}
            if v is None:
                self.prefs.pop(key, None)
            else:
                self.prefs[key] = v
            self._note("set", key=key, size=len(v or ""), dropped=False)
            return {}
        if op == "Preferences.remove":
            self.prefs.pop(key, None)
            self._note("remove", key=key)
            return {}
        if op == "Preferences.clear":
            self.prefs.clear()
            self._note("clear")
            return {}
        if op == "Preferences.keys":
            self._note("keys")
            return {"keys": list(self.prefs)}
        if op == "Filesystem.writeFile":
            path, data = a.get("path"), a.get("data")
            if not isinstance(path, str) or not isinstance(data, str):
                return {"__error": "The 'writeFile' input parameters aren't valid."}
            uri = self._uri(path, a.get("directory"))
            self.files[path] = {"path": path, "data": data, "directory": a.get("directory"),
                                "encoding": a.get("encoding"), "uri": uri}
            self._note("writeFile", path=path, directory=a.get("directory"), size=len(data))
            return {"uri": uri}
        if op in ("Filesystem.readFile", "Filesystem.deleteFile"):
            path = a.get("path")
            name = op.split(".")[1]
            if not isinstance(path, str):
                return {"__error": "The '%s' input parameters aren't valid." % name}
            if path not in self.files:
                return {"__error": "'%s' failed because file at '%s' does not exist." % (name, path)}
            self._note(name, path=path)
            if name == "readFile":
                return {"data": self.files[path]["data"]}
            del self.files[path]
            return {}
        if op == "Filesystem.getUri":
            path = a.get("path")
            if not isinstance(path, str):
                return {"__error": "The 'getUri' input parameters aren't valid."}
            return {"uri": self._uri(path, a.get("directory"))}
        if op == "Share.share":
            files = a.get("files")
            if a.get("text") is None and a.get("url") is None and not files:
                return {"__error": "Must provide a URL or Message or files"}
            if files and not all(isinstance(f, str) and f.startswith("file://") for f in files):
                return {"__error": "only file urls are supported"}
            self.shares.append(dict(a))
            self._note("share", files=list(files or []))
            return {"activityType": ""}
        return {"__error": "fake bridge has no method " + op}


# ---------------------------------------------------------------- fixtures --------------------

class Fixture:
    """One saved state to replay. Two formats (tests/saves/README.md):
      export envelope   {"kind":"bodhya-progress","v":1,"app":...,"savedAt":...,"state":{...}}
                        what Save my progress writes; it seeds hikmat_state_guest
      storage snapshot  {"kind":"storage-snapshot","note":...,"localStorage":{key: rawString},
                         "withCaches"?: true, "fillToQuota"?: true}
                        a whole origin's localStorage, for states a single key cannot describe
    """

    def __init__(self, name, doc, private=False):
        self.name = name
        self.doc = doc
        # A tester's real save is a child's data. Its checks print where something went wrong,
        # never the values themselves (her settings, her totals), so a run's output can be shared
        # or land in a session log without carrying any of it. See tests/saves/README.md.
        self.private = private
        kind = doc.get("kind") if isinstance(doc, dict) else None
        if kind == "bodhya-progress":
            if not isinstance(doc.get("state"), dict):
                raise ValueError("an export envelope needs a state object")
            self.local = {"hikmat_state_guest": compact(doc["state"])}
            self.with_caches = self.fill = False
        elif kind == "storage-snapshot":
            ls = doc.get("localStorage")
            if not isinstance(ls, dict) or not all(isinstance(v, str) for v in ls.values()):
                raise ValueError("a storage snapshot needs localStorage: {key: rawString}")
            self.local = dict(ls)
            self.with_caches = bool(doc.get("withCaches"))
            self.fill = bool(doc.get("fillToQuota"))
        else:
            raise ValueError("unknown fixture kind %r" % (kind,))
        self.seed_states = {}
        for k, raw in self.local.items():
            if k.startswith("hikmat_state_"):
                try:
                    self.seed_states[k] = json.loads(raw)
                except ValueError:
                    self.seed_states[k] = None


def load_fixture(path, name, private=False):
    return Fixture(name, json.loads(Path(path).read_text(encoding="utf-8")), private)


@functools.lru_cache(maxsize=1)
def cache_values():
    """The three rebuildable content caches at the size a phone really holds them. The course and
    test bank caches are the bundled files, re-serialised the way the game writes them. The
    structure cache is what get_structure() serves: the published grade bands and the subjects,
    read from the seed data in setup_data.py, because curriculum.json carries no structure."""
    found = {}
    for node in ast.parse(SETUP_DATA.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("GRADE_BANDS", "SUBJECTS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    if set(found) != {"GRADE_BANDS", "SUBJECTS"}:
        raise RuntimeError("setup_data.py no longer has literal GRADE_BANDS and SUBJECTS")
    structure = {
        "bands": [{"key": b["key"], "title": b["title"], "titleHi": b["titleHi"],
                   "subtitle": b.get("subtitle") or "", "subtitleHi": b.get("subtitleHi") or "",
                   "icon": b.get("icon") or "📚", "color": b.get("color") or "#6c5ce7"}
                  for b in found["GRADE_BANDS"]],
        "subjects": [{"key": s["key"], "title": s["title"], "titleHi": s["titleHi"],
                      "icon": s.get("icon") or "📘", "color": s.get("color") or "#6c5ce7"}
                     for s in found["SUBJECTS"]],
    }
    testbank = json.loads((PUBLIC / "testbank.json").read_text(encoding="utf-8"))
    return dict(zip(CACHE_KEYS, (compact(CURRICULUM), compact(structure), compact(testbank))))


def fixture_paths():
    """(test name, path) for every committed fixture, then every private tester save."""
    out = [("update:" + p.stem, p) for p in sorted(SAVES.glob("*.json"))]
    if PRIVATE.is_dir():
        out += [("update:private/" + p.stem, p) for p in sorted(PRIVATE.glob("*.json"))]
    return out


# ---------------------------------------------------------------- server and phones -----------

class _Static(http.server.SimpleHTTPRequestHandler):
    """hikmat/public/ under /assets/hikmat/ and nothing else. Every other path is a 404, which is
    exactly what a static host answers for the hikmat.api.* calls."""

    def log_message(self, *a):
        pass

    def translate_path(self, path):
        p = urlsplit(path).path
        if not p.startswith(PREFIX):
            return str(PUBLIC / "__not_served__")
        return super().translate_path("/" + p[len(PREFIX):])

    def list_directory(self, path):
        self.send_error(404)
        return None

    def send_error(self, code, message=None, explain=None):
        # No body. Chromium counts a request as finished only once its body has been read, by the
        # page or by its HTTP cache, and the game never reads a failed response. A 404 body the
        # cache will not take (Cache-Control: no-store) therefore stays open forever, the network
        # never goes idle, and the boot wait never ends. An empty body leaves nothing to read.
        self.send_response(code, message)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()


def start_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Static, directory=str(PUBLIC)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Env:
    """One browser in one mode. Each phone() is a fresh browser context: a phone of its own."""

    def __init__(self, browser, base, mode, old_ref, old_html, new_html):
        self.browser, self.base, self.mode = browser, base, mode
        self.netloc = urlsplit(base).netloc
        self.old_ref, self.old_html, self.new_html = old_ref, old_html, new_html

    def phone(self, sabotage=None):
        return Phone(self, sabotage)

    def sabotaged(self, script):
        """The new build with `script` run after the game's own, for --self-test."""
        i = self.new_html.rfind("</body>")
        if i < 0:
            raise RuntimeError("game.html has no </body> to append a sabotage before")
        return self.new_html[:i] + script + self.new_html[i:]


class Phone:
    """One phone: its own origin storage, and in native mode its own NATIVE store."""

    def __init__(self, env, sabotage=None):
        self.env = env
        self.sabotage = sabotage
        self.errors = []         # page errors, console errors and refused requests, all launches
        self.left = []           # every url the phone tried to reach outside the local server
        self.page = None
        self.boot_result = {}
        self._answers = []
        self._expected = [API_PATH]
        self.ctx = env.browser.new_context(service_workers="block", viewport=VIEWPORT)
        self.ctx.route("**/*", self._route)
        self.NATIVE = Native() if env.mode == "native" else None
        if self.NATIVE is not None:
            self.ctx.expose_binding("__bodhyaNative", lambda source, op, args=None: self.NATIVE.call(op, args))
            self.ctx.add_init_script(NATIVE_JS)

    # -- network --------------------------------------------------------------------------
    def answer(self, path_re, status=404, body="", content_type="text/plain", abort=False):
        """Answer local requests whose path matches `path_re` here instead of from disk: abort them
        (an offline phone) or fulfill them with `status`. Their load failures are expected, so
        they are not counted as console errors. The latest rule wins."""
        rx = re.compile(path_re)
        self._answers.insert(0, (rx, status, body, content_type, abort))
        self._expected.append(rx)

    def _route(self, route):
        url = route.request.url
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or u.netloc != self.env.netloc:
            self.left.append(url)
            self.errors.append("tried to reach %s://%s%s, and this harness is offline" % (u.scheme, u.netloc, u.path))
            return route.abort()
        html = None
        if u.path == OLD_PATH:
            html = self.env.old_html
        elif u.path == SEED_PATH:
            html = SEED_HTML
        elif u.path == NEW_PATH and self.sabotage:
            html = self.env.sabotaged(self.sabotage)
        if html is not None:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        for rx, status, body, content_type, abort in self._answers:
            if rx.search(u.path):
                return route.abort() if abort else route.fulfill(status=status, content_type=content_type, body=body)
        return route.continue_()

    def _expected_failure(self, url):
        if url in self.left:
            return True             # already recorded, once, as a refused request
        u = urlsplit(url)
        return u.netloc == self.env.netloc and any(rx.search(u.path) for rx in self._expected)

    def _on_console(self, msg):
        if msg.type != "error":
            return
        url = (msg.location or {}).get("url") or ""
        if msg.text.startswith("Failed to load resource") and self._expected_failure(url):
            return
        self.errors.append("console.error: %s%s" % (msg.text[:300], " (%s)" % url if url else ""))

    def _on_pageerror(self, err):
        self.errors.append("pageerror: %s" % (getattr(err, "message", None) or err))

    # -- storage --------------------------------------------------------------------------
    def seed_local(self, local, with_caches=False, fill=False):
        """Make this phone's localStorage exactly `local` ({key: rawString}), on the game's own
        origin, from a page that runs no game code."""
        page = self.ctx.new_page()
        try:
            page.goto(self.env.base + SEED_PATH)
            page.evaluate("(entries) => { localStorage.clear(); for (const [k, v] of entries) localStorage.setItem(k, v); }",
                          list(local.items()))
            if with_caches:
                page.evaluate("(entries) => { for (const [k, v] of entries) localStorage.setItem(k, v); }",
                              list(cache_values().items()))
            if fill:
                r = page.evaluate(FILL_JS)
                if not r["full"]:
                    raise RuntimeError("localStorage never filled up (%d filler keys)" % r["n"])
        finally:
            page.close()

    def seed(self, fx):
        self.seed_local(fx.local, fx.with_caches, fx.fill)

    # -- launches -------------------------------------------------------------------------
    def launch(self, which="new", wait=True, timeout=20000):
        """Open the app the way a phone does after the app was swiped away: a new page, so nothing
        in memory survives, on the same origin, so everything in storage does."""
        if self.page is not None:
            self.page.close()
        page = self.ctx.new_page()
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        self.page = page
        page.goto(self.env.base + (OLD_PATH if which == "old" else NEW_PATH))
        if wait:
            self.boot(timeout)
        return page

    def boot(self, timeout=20000):
        """The plan's boot wait: the real curriculum is in, then contentReady and nativeRestore
        are awaited when the build has them. After that the network must go idle, because the
        level depends on the test bank and the boot callback runs after the API answers."""
        self.page.wait_for_function("typeof COURSES !== 'undefined' && COURSES.length > 5", timeout=timeout)
        self.boot_result = self.page.evaluate(BOOT_JS) or {}
        for k, v in self.boot_result.items():
            if k.endswith("Error"):
                self.errors.append("%s rejected: %s" % (k[:-len("Error")], str(v)[:300]))
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except PWTimeout:
            self.errors.append("the network never went idle after boot")
        self.page.evaluate("() => new Promise(r => setTimeout(r, 30))")

    def snap(self):
        return self.page.evaluate(SNAP_JS)

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass


# ---------------------------------------------------------------- reporting -------------------

class Report:
    def __init__(self):
        self.passed = 0
        self.fails = []

    def check(self, cond, msg):
        print(("PASS " if cond else "FAIL ") + msg, flush=True)
        if cond:
            self.passed += 1
        else:
            self.fails.append(msg)
        return bool(cond)


def diff(a, b, path="", out=None, limit=6, values=True):
    """Where two JSON values differ, as short 'path old -> new' strings (just 'path changed'
    when values=False)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = "%s.%s" % (path, k) if path else str(k)
            if k not in a:
                out.append(p + " appeared")
            elif k not in b:
                out.append(p + " disappeared")
            else:
                diff(a[k], b[k], p, out, limit, values)
            if len(out) >= limit:
                break
    elif a != b:
        if not values:
            out.append(path + " changed")
        elif isinstance(a, list) and isinstance(b, list):
            out.append("%s changed (%d -> %d items)" % (path, len(a), len(b)))
        else:
            out.append("%s %s -> %s" % (path, json.dumps(a)[:60], json.dumps(b)[:60]))
    return out


def _clip(items, n=6):
    items = list(items)
    return "; ".join(items[:n]) + (" (+%d more)" % (len(items) - n) if len(items) > n else "")


# ---------------------------------------------------------------- the update checks -----------

def lowered(seed, got, key, values=True):
    """Every way `got` is worse than `seed` in the fields I1 guards, as 'key path old -> new'
    (as 'key path dropped' when values=False)."""
    if not isinstance(got, dict):
        return ["%s is no longer a saved state" % key]
    if not isinstance(seed, dict):
        return []
    out = []

    def drop(path, s, g):
        out.append("%s %s %s" % (key, path, "%s -> %s" % (s, g) if values else "dropped"))

    gp = _dict(got.get("progress"))
    for ck, lessons in _dict(seed.get("progress")).items():
        for lk, games in _dict(lessons).items():
            for aid, v in _dict(games).items():
                s = _num(v)
                if s is None:
                    continue
                g = _num(_dict(_dict(gp.get(ck)).get(lk)).get(aid)) or 0
                if g < s:
                    drop("progress.%s.%s.%s" % (ck, lk, aid), v, g)
    s, g = _num(seed.get("coins")), _num(got.get("coins")) or 0
    if s is not None and g < s:
        drop("coins", s, g)
    gt = _dict(got.get("levelTests"))
    for n, t in _dict(seed.get("levelTests")).items():
        t, h = _dict(t), _dict(gt.get(n))
        for f in ("bestPct", "attempts"):
            s, g = _num(t.get(f)), _num(h.get(f)) or 0
            if s is not None and g < s:
                drop("levelTests.%s.%s" % (n, f), s, g)
        if t.get("passed") and not h.get("passed"):
            out.append("%s levelTests.%s.passed turned %s" % (key, n, json.dumps(h.get("passed"))))
    return out


def judge(fx, A, B, B3, R, errors, tag, old_ref):
    """I1-I5, R and the error check for one fixture, as [(code, ok, message)]."""
    shown = not fx.private
    out = []
    probs = []
    for key, seed_state in fx.seed_states.items():
        got = B["stateKeys"].get(key)
        if not isinstance(got, dict) or "__unparseable__" in got:
            probs.append("%s missing" % key)
        else:
            probs += lowered(seed_state, got, key, shown)
    out.append(("I1", not probs, "%s I1 %s" % (tag, _clip(probs) if probs else
                "every seeded star, gem and test result is still there (%d state keys)" % len(fx.seed_states))))
    ok = B["total"] >= A["total"] and B["level"] >= A["level"]
    out.append(("I2", ok, "%s I2 %s" % (tag, "total %s -> %s, level %s -> %s" % (A["total"], B["total"], A["level"], B["level"])
                if shown else "total and level %s" % ("kept" if ok else "dropped"))))
    lost = sorted(set(map(tuple, A["playedOpen"])) - set(map(tuple, B["playedOpen"])))
    out.append(("I3", not lost, "%s I3 %s" % (tag, "closed by the update: " + _clip("%s/%s" % x for x in lost) if lost else
                "all %d played lessons still open" % len(A["playedOpen"]))))
    d = diff(A["settings"], B["settings"], values=shown)
    s = B["settings"]
    out.append(("I4", not d, "%s I4 %s" % (tag, "settings changed: " + _clip(d) if d else "settings unchanged" +
                (" (%s, %s, %s, %s)" % (s["lang"], s["theme"], s["gender"], s["mascot"]) if shown else ""))))
    d = diff(B, B3, values=shown)
    out.append(("I5", not d, "%s I5 %s" % (tag, "a relaunch changed " + _clip(d) if d else "three relaunches change nothing")))
    rp = []
    if R["total"] != B["total"]:
        rp.append("total %s -> %s" % (B["total"], R["total"]) if shown else "total changed")
    if R["level"] != B["level"]:
        rp.append("level %s -> %s" % (B["level"], R["level"]) if shown else "level changed")
    shut = sorted(set(map(tuple, B["playedOpen"])) - set(map(tuple, R["playedOpen"])))
    if shut:
        rp.append("closed: " + ", ".join("%s/%s" % x for x in shut[:5]))
    if R["coursesLen"] != CURRICULUM_LEN:
        rp.append("%s tracks, not the %d in curriculum.json" % (R["coursesLen"], CURRICULUM_LEN))
    out.append(("R", not rp, "%s R  %s %s" % (tag, old_ref, "rollback broke: " + _clip(rp) if rp else
                "reads it back: %s stars, level %s, %d played lessons open, %d tracks"
                % (R["total"], R["level"], len(R["playedOpen"]), R["coursesLen"]) if shown else
                "reads it back: same total and level, %d played lessons open, %d tracks"
                % (len(R["playedOpen"]), R["coursesLen"]))))
    out.append(("errors", not errors, "%s %s" % (tag, _clip(errors, 3) if errors else "no page or console errors")))
    return out


def update_results(env, fx, sabotage=None):
    """Seed, open the old build (A), the new build (B), relaunch it three times (B3), then open
    the old build again (R), and judge the lot."""
    ph = env.phone(sabotage=sabotage)
    try:
        ph.seed(fx)
        ph.launch("old")
        A = ph.snap()
        ph.launch("new")
        B = ph.snap()
        for _ in range(3):
            ph.launch("new")
        B3 = ph.snap()
        ph.launch("old")
        R = ph.snap()
        errors = list(ph.errors)
    finally:
        ph.close()
    return judge(fx, A, B, B3, R, errors, "[%s] %s" % (env.mode, fx.name), env.old_ref)


# ---------------------------------------------------------------- tests -----------------------

class Test:
    def __init__(self, name, fn, modes=("web", "native"), once=False):
        self.name, self.fn, self.modes, self.once = name, fn, modes, once


TESTS = []


def test(name, modes=("web", "native"), once=False):
    def register(fn):
        TESTS.append(Test(name, fn, modes, once))
        return fn
    return register


def t_update(path, name):
    def run(env, report):
        label = name.split(":", 1)[1]
        fx = load_fixture(path, label, private=label.startswith("private/"))
        for _code, ok, msg in update_results(env, fx):
            report.check(ok, msg)
    return run


@test("harness")
def t_harness(env, report):
    """The harness itself: it must stay offline, and the native fake must behave like the real
    plugins, because the native tests later in this plan lean on both."""
    tag = "[%s] harness" % env.mode
    ph = env.phone()
    try:
        ph.seed_local({})
        ph.launch("new")
        r = ph.page.evaluate("() => fetch('https://learn.bodhya.net/api/method/hikmat.api.get_courses')"
                             ".then(() => 'reached', () => 'refused')")
        report.check(r == "refused" and any("learn.bodhya.net" in u for u in ph.left),
                     "%s a request to learn.bodhya.net is refused and recorded" % tag)
        ph.errors = [e for e in ph.errors if "learn.bodhya.net" not in e]
        if ph.NATIVE is not None:
            N = ph.NATIVE
            r = ph.page.evaluate(r"""async () => {
              const P = Capacitor.Plugins.Preferences, F = Capacitor.Plugins.Filesystem, S = Capacitor.Plugins.Share;
              const out = {};
              out.missing = await P.get({ key: "harness_probe" });
              await P.set({ key: "harness_probe", value: "one" });
              out.got = await P.get({ key: "harness_probe" });
              out.keys = await P.keys();
              try { await P.set({ value: "x" }); out.noKey = "resolved"; } catch (e) { out.noKey = e.message; }
              out.file = await F.writeFile({ path: "probe.json", data: "{}", directory: "CACHE", encoding: "utf8" });
              try { await F.writeFile({ path: "x.json", directory: "CACHE" }); out.noData = "resolved"; } catch (e) { out.noData = e.message; }
              out.share = await S.share({ title: "probe", files: [out.file.uri] });
              try { await S.share({ title: "probe" }); out.emptyShare = "resolved"; } catch (e) { out.emptyShare = e.message; }
              return out;
            }""")
            report.check(r["missing"] == {"value": None} and r["got"] == {"value": "one"}
                         and r["keys"] == {"keys": ["harness_probe"]} and r["noKey"] == "Must provide key",
                         "%s fake Preferences: null for a missing key, set/get/keys, 'Must provide key'" % tag)
            report.check(r["file"] == {"uri": "file:///cache/probe.json"}
                         and N.files.get("probe.json", {}).get("directory") == "CACHE"
                         and N.files["probe.json"]["data"] == "{}"
                         and r["noData"] == "The 'writeFile' input parameters aren't valid.",
                         "%s fake Filesystem.writeFile records the data and returns file:///cache/<path>" % tag)
            report.check(r["share"] == {"activityType": ""} and N.shares[-1].get("files") == ["file:///cache/probe.json"]
                         and r["emptyShare"] == "Must provide a URL or Message or files",
                         "%s fake Share.share records the call and refuses an empty share" % tag)
            ph.page.evaluate("() => localStorage.clear()")
            ph.launch("new")
            v = ph.page.evaluate("() => Capacitor.Plugins.Preferences.get({ key: 'harness_probe' })")
            report.check(v == {"value": "one"}, "%s NATIVE outlives localStorage.clear() and a relaunch" % tag)
            N.drop_next_write = True
            v = ph.page.evaluate("""async () => { const P = Capacitor.Plugins.Preferences;
                                     await P.set({ key: 'harness_probe', value: 'two' });
                                     return (await P.get({ key: 'harness_probe' })).value; }""")
            dropped = [e for e in N.log if e["op"] == "set" and e.get("dropped")]
            report.check(v == "one" and N.drop_next_write is False and len(dropped) == 1,
                         "%s drop_next_write loses exactly the next set, and the log says so" % tag)
            N.get_delay_ms = 300
            r = ph.page.evaluate("""async () => { const P = Capacitor.Plugins.Preferences, t0 = performance.now();
                                     const g = P.get({ key: 'harness_race' });
                                     await new Promise(r => setTimeout(r, 60));
                                     await P.set({ key: 'harness_race', value: 'written-mid-read' });
                                     const v = await g;
                                     return { value: v.value, ms: performance.now() - t0 }; }""")
            N.get_delay_ms = 0
            order = [e["op"] for e in N.log if e.get("key") == "harness_race"]
            early = N.writes_before_read("harness_race")
            report.check(r["ms"] >= 290 and order == ["get:begin", "set", "get"] and len(early) == 1
                         and r["value"] == "written-mid-read",
                         "%s get_delay_ms holds a read open, and a write inside it shows in writes_before_read()" % tag)
        report.check(not ph.errors, "%s %s" % (tag, _clip(ph.errors, 3) if ph.errors else "no page or console errors"))
    finally:
        ph.close()


def _played(fx):
    """A deterministic played star in the fixture's guest state that the curriculum still has."""
    prog = _dict(_dict(fx.seed_states.get("hikmat_state_guest")).get("progress"))
    for ck in sorted(prog):
        for lk in sorted(_dict(prog[ck])):
            if (ck, lk) not in CURRICULUM_LESSONS:
                continue
            for aid, v in sorted(_dict(prog[ck][lk]).items()):
                if (_num(v) or 0) > 0:
                    return ck, lk, aid
    raise RuntimeError("%s has no played star to sabotage" % fx.name)


@test("self-test", once=True)
def t_self_test(env, report):
    """The checks must be able to fail. Two broken builds go through the same update as v7-midway:
    one loses a star, one shuts a lesson she has played. Each must be caught by the right check."""
    tag = "[%s] self-test" % env.mode
    fx = load_fixture(SAVES / "v7-midway.json", "v7-midway (sabotaged)")
    ck, lk, aid = _played(fx)

    def run(script):
        res = update_results(env, fx, sabotage=script)
        for code, ok, msg in res:
            if not ok:
                print("    caught: " + msg)
        return {code: (ok, msg) for code, ok, msg in res}

    lose = ("<script>/* e2e self-test: one played star is lost */(function(){var c=%s,l=%s,a=%s;"
            "var g=((state.progress||{})[c]||{})[l];if(g&&g[a]){g[a]=0;save();}})();</script>"
            % (json.dumps(ck), json.dumps(lk), json.dumps(aid)))
    res = run(lose)
    report.check(not res["I1"][0] and ("%s.%s.%s" % (ck, lk, aid)) in res["I1"][1],
                 "%s a star set to 0 fails I1, naming progress.%s.%s.%s" % (tag, ck, lk, aid))
    report.check(not res["I2"][0], "%s the same lost star fails I2: the total dropped" % tag)
    shut = ("<script>/* e2e self-test: one played lesson is shut */(function(){var c=%s,l=%s,orig=lessonLock;"
            "lessonLock=function(course,ls){if(course&&ls&&course.key===c&&ls.key===l)return{kind:'level',level:5};"
            "return orig.apply(this,arguments);};})();</script>" % (json.dumps(ck), json.dumps(lk)))
    res = run(shut)
    report.check(not res["I3"][0] and ("%s/%s" % (ck, lk)) in res["I3"][1],
                 "%s a played lesson shut by lessonLock fails I3, naming %s/%s" % (tag, ck, lk))


# ---------------------------------------------------------------- --make-fixtures -------------

SAVED_AT = "2026-11-02T10:00:00.000Z"       # fixed, so regenerating gives the same files
STARS = (3, 2, 3, 1, 3, 3, 2, 1, 3, 2, 3)   # a varied but repeatable spread of 1-3 stars
OLD_TYPES = ("learn", "listen", "spell", "phrase", "talk", "quiz")   # the activities of early 2026


def build_fixtures(cat):
    courses = {c["key"]: c for c in cat["courses"]}
    bank = defaultdict(list)
    for qid, t, l in cat["bank"]:
        bank[(t, l)].append(qid)
    level = {(c["key"], ls["key"]): ls["level"] for c in cat["courses"] for ls in c["lessons"]}
    gtype = {(c["key"], ls["key"], g): t for c in cat["courses"] for ls in c["lessons"] for g, t in ls["games"]}
    girls = [m for m, male in cat["mascots"] if not male]
    boys = [m for m, male in cat["mascots"] if male]
    life = [c["key"] for c in cat["courses"] if not c["band"]]

    def track(ck):
        if ck not in courses or not courses[ck]["lessons"]:
            raise RuntimeError("the old build has no track %r to build fixtures from" % ck)
        return courses[ck]["lessons"]

    def lk(ck, i):
        return track(ck)[i]["key"]

    def play(progress, ck, idxs, offset=0, partial=(), types=None):
        """Stars on lessons `idxs` of track ck: every game, except two games on a `partial` lesson
        (one she is in the middle of). `types` limits it to the activities an old build had."""
        lessons = track(ck)
        for i in idxs:
            games = [g for g, t in lessons[i]["games"] if types is None or t in types]
            if i in partial:
                games = games[:2]
            if not games:
                continue
            row = progress.setdefault(ck, {}).setdefault(lessons[i]["key"], {})
            for j, g in enumerate(games):
                row[g] = STARS[(i + j + offset) % len(STARS)]
        return progress

    def seen_types(progress):
        return {t: 1 for t in sorted({gtype[(ck, l, g)] for ck in progress for l in progress[ck] for g in progress[ck][l]})}

    def seen_ids(progress, n, count):
        ids = [q for ck in progress for l in progress[ck] if level.get((ck, l)) == n for q in bank[(ck, l)]]
        return ids[:count]

    def envelope(state):
        return {"kind": "bodhya-progress", "v": 1, "app": APP_ID, "savedAt": SAVED_AT, "state": state}

    def snapshot(note, local, **extra):
        return dict({"kind": "storage-snapshot", "note": note, "localStorage": local}, **extra)

    # v7-midway: a girl halfway through six tracks of v7, every field v7 writes, settings changed.
    mid = {}
    for ck, n in (("eng-foundation", 7), ("math-early", 7), ("evs-myworld", 7),
                  ("hindi-aksharmala", 7), ("bazaar", 6), ("home", 6)):
        play(mid, ck, range(n), partial={n - 1})
    midway = {
        "progress": mid, "coins": 1240, "hindi": False, "lang": "hi", "sound": True,
        "testSeen": {"L1": seen_ids(mid, 1, 40), "L2": seen_ids(mid, 2, 20)},
        "levelTests": {"1": {"passed": True, "bestPct": 85, "attempts": 2}},
        "videosSeen": {"eng-foundation": 1, "math-early": 1,
                       "eng-foundation::" + lk("eng-foundation", 1): 1, "bazaar::" + lk("bazaar", 0): 1},
        "seenTypes": seen_types(mid),
        "theme": "dark", "prefsTouched": True, "gender": "Female", "mascot": girls[1],
        "lastLoc": {"class": {"ck": "hindi-aksharmala", "lk": lk("hindi-aksharmala", 6)},
                    "subject": {"ck": "math-early", "lk": lk("math-early", 6)},
                    "life": {"ck": "home", "lk": lk("home", 5)}},
        "lastMode": "class",
    }

    # pre-ledger: a save from before seenTypes, levelTests, testSeen and videosSeen existed, from
    # the days of the first six activities, with the old Hindi-gloss toggle still switched on.
    old = {}
    play(old, "eng-foundation", range(6), offset=3, types=OLD_TYPES)
    play(old, "bazaar", range(6), offset=3, types=OLD_TYPES)
    pre_ledger = {"progress": old, "coins": 520, "hindi": True, "lang": "en", "sound": True}

    # retired-account: a girl who was signed in when accounts were switched off. Her account's
    # lessons overlap v7-midway's in two tracks and add two tracks it does not have.
    acct = {}
    play(acct, "eng-foundation", range(4, 9), offset=5)
    play(acct, "math-early", range(5, 9), offset=5)
    play(acct, "sci-around", range(4), offset=5)
    play(acct, "money", range(2), offset=5)
    acct_state = {
        "progress": acct, "coins": 410, "hindi": False, "lang": "hi", "sound": True,
        "testSeen": {"L1": seen_ids(acct, 1, 20), "L2": seen_ids(acct, 2, 20)},
        "levelTests": {"1": {"passed": True, "bestPct": 90, "attempts": 1},
                       "2": {"passed": False, "bestPct": 55, "attempts": 1}},
        "videosSeen": {"sci-around": 1}, "seenTypes": seen_types(acct),
        "theme": "light", "prefsTouched": True,
        "lastLoc": {"subject": {"ck": "sci-around", "lk": lk("sci-around", 3)}}, "lastMode": "subject",
    }
    guest = {}
    play(guest, "school", range(2), offset=2)
    guest_state = {"progress": guest, "coins": 25, "hindi": False, "lang": "en", "sound": True,
                   "testSeen": {}, "levelTests": {}, "videosSeen": {}, "seenTypes": seen_types(guest)}
    current = {"id": "S-0042", "name": "Test Learner", "gender": "Male", "mascot": boys[1]}

    # two-accounts: two signed-in learners on one phone, hikmat_current naming the first.
    a, b = {}, {}
    play(a, "eng-grammar", range(5), offset=1)
    play(a, "math-numbers", range(5), offset=1)
    play(b, "sci-physics", range(4), offset=7)
    play(b, "comp-basics", range(4), offset=7)
    a_state = {"progress": a, "coins": 300, "hindi": False, "lang": "hi", "sound": True,
               "testSeen": {"L1": seen_ids(a, 1, 20)},
               "levelTests": {"1": {"passed": False, "bestPct": 60, "attempts": 1}},
               "videosSeen": {}, "seenTypes": seen_types(a),
               "theme": "light", "prefsTouched": True, "gender": "Female", "mascot": girls[3]}
    b_state = {"progress": b, "coins": 180, "hindi": False, "lang": "en", "sound": True,
               "testSeen": {}, "levelTests": {}, "videosSeen": {}, "seenTypes": seen_types(b),
               "gender": "Female", "mascot": girls[2]}

    # heavy: everything there is, at the top of every scale.
    h = {}
    for c in cat["courses"]:
        for ls in c["lessons"]:
            if ls["games"]:
                h.setdefault(c["key"], {})[ls["key"]] = {g: 3 for g, _t in ls["games"]}
    first = {m: next(ck for ck in sorted(h) if (ck in life) == (m == "life")) for m in ("class", "life")}
    heavy = {
        "progress": h, "coins": 99999, "hindi": False, "lang": "en", "sound": False,
        "testSeen": {"L%d" % n: seen_ids(h, n, 40) for n in range(1, 6)},
        "levelTests": {str(n): {"passed": True, "bestPct": 76 + 4 * n, "attempts": n} for n in range(1, 6)},
        "videosSeen": {c["key"]: 1 for c in cat["courses"]},
        "seenTypes": seen_types(h),
        "theme": "light", "prefsTouched": True, "gender": "Other", "mascot": girls[0],
        "lastLoc": {"class": {"ck": first["class"], "lk": lk(first["class"], 0)},
                    "subject": {"ck": first["class"], "lk": lk(first["class"], 1)},
                    "life": {"ck": first["life"], "lk": lk(first["life"], 0)}},
        "lastMode": "life",
    }

    return {
        "v7-midway": envelope(midway),
        "pre-ledger": snapshot(
            "A save from before seenTypes, levelTests, testSeen and videosSeen existed: only the "
            "first six activities, gems, and the old Hindi-gloss toggle still on.",
            {"hikmat_state_guest": compact(pre_ledger)}),
        "retired-account": snapshot(
            "Signed in as S-0042 when accounts were switched off. retireAccounts() must fold her "
            "account into the guest profile and leave hikmat_state_S-0042 where it is.",
            {"hikmat_current": compact(current), "hikmat_state_S-0042": compact(acct_state),
             "hikmat_state_guest": compact(guest_state)}),
        "two-accounts": snapshot(
            "Two signed-in learners on one phone, hikmat_current naming A. B's key must survive "
            "untouched.",
            {"hikmat_current": compact({"id": "A", "name": "Learner A", "gender": "Female", "mascot": girls[3]}),
             "hikmat_state_A": compact(a_state), "hikmat_state_B": compact(b_state)}),
        "full-storage": snapshot(
            "v7-midway on a phone whose localStorage is full: the three content caches at real "
            "size, then filler until setItem throws.",
            {"hikmat_state_guest": compact(midway)}, withCaches=True, fillToQuota=True),
        "heavy": envelope(heavy),
    }


def make_fixtures(env):
    ph = env.phone()
    try:
        ph.seed_local({})
        ph.launch("old")
        cat = ph.page.evaluate(CATALOG_JS)
        errors = list(ph.errors)
    finally:
        ph.close()
    if errors:
        raise SystemExit("%s raised errors while the fixtures were built: %s" % (env.old_ref, _clip(errors, 3)))
    SAVES.mkdir(parents=True, exist_ok=True)
    for name, doc in build_fixtures(cat).items():
        path = SAVES / (name + ".json")
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        fx = Fixture(name, doc)
        stars = sum(_num(v) or 0 for st in fx.seed_states.values() for c in _dict(_dict(st).get("progress")).values()
                    for l in _dict(c).values() for v in _dict(l).values())
        lessons = sum(len(_dict(c)) for st in fx.seed_states.values() for c in _dict(_dict(st).get("progress")).values())
        print("wrote %s (%d state keys, %d lessons, %d stars)" % (path.relative_to(ROOT), len(fx.seed_states), lessons, stars))


# ---------------------------------------------------------------- main ------------------------

def main():
    ap = argparse.ArgumentParser(description="Replay saved progress through the update and back.")
    ap.add_argument("--old", default="play-v7", help="git ref of the build she has now (default play-v7)")
    ap.add_argument("--mode", choices=("web", "native", "both"), default="both")
    ap.add_argument("--only", help="run only tests whose name contains this")
    ap.add_argument("--self-test", action="store_true", help="only prove that the checks can fail")
    ap.add_argument("--make-fixtures", action="store_true", help="rebuild tests/saves/*.json from --old")
    args = ap.parse_args()

    try:
        old_html = subprocess.run(["git", "-C", str(ROOT), "show", "%s:hikmat/public/game.html" % args.old],
                                  check=True, capture_output=True).stdout.decode("utf-8")
    except subprocess.CalledProcessError as e:
        sys.exit("cannot read hikmat/public/game.html at %s: %s" % (args.old, e.stderr.decode().strip()))
    new_html = (PUBLIC / "game.html").read_text(encoding="utf-8")

    tests = [Test(name, t_update(path, name)) for name, path in fixture_paths()] + TESTS
    if args.self_test:
        tests = [t for t in tests if t.name == "self-test"]
    elif args.only:
        tests = [t for t in tests if args.only == t.name or args.only in t.name]
    modes = ["web", "native"] if args.mode == "both" else [args.mode]

    srv = start_server()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    report = Report()
    t0 = time.time()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                if args.make_fixtures:
                    make_fixtures(Env(browser, base, "web", args.old, old_html, new_html))
                    return 0
                if not fixture_paths():
                    report.check(False, "no fixtures in tests/saves/: run --make-fixtures first")
                if not tests:
                    report.check(False, "no test matches --only %r" % args.only)
                for mode in modes:
                    env = Env(browser, base, mode, args.old, old_html, new_html)
                    for t in tests:
                        if mode not in t.modes or (t.once and mode != modes[0]):
                            continue
                        print("\n-- [%s] %s" % (mode, t.name), flush=True)
                        try:
                            t.fn(env, report)
                        except Exception as e:
                            report.check(False, "[%s] %s crashed: %s: %s" % (mode, t.name, type(e).__name__, str(e)[:300]))
            finally:
                browser.close()
    finally:
        srv.shutdown()
    print("\n== %d passed, %d failed (%s, old=%s, %.0fs)" % (report.passed, len(report.fails), "+".join(modes),
                                                            args.old, time.time() - t0))
    print("FAILS", len(report.fails), report.fails)
    return 1 if report.fails else 0


if __name__ == "__main__":
    sys.exit(main())
