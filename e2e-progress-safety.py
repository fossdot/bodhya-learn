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
phone. She relaunches three times and saves once. Last, the old build opens what the new one left,
because a release that has to be pulled must hand her back the same progress.

Every fixture also runs a second time, on a second fresh phone, "direct": the new build opens the
seed itself, with no v7 launch first. Play installs the newest APK over whatever is on the phone, so
a phone last opened before accounts went web-only (27 Aug 2026) meets the new build with
hikmat_current still there and the account fold still to do, and only the new build's own fold runs.
The direct run is judged against what v7 left in the first run (A), so that fold has to keep
everything v7's keeps.

The checks (docs/plans/2026-11-03-step-0-progress-safety.md, Task 1), each named with the fixture,
and with "direct" for the second run:
  I1  no hikmat_state_* key that is in the seed, or that v7 left (A), goes missing. No star,
      coins, bestPct or attempts drops below either, and no passed test turns false. And, for rule
      1 (never delete, rename or reshape a stored field): no field is deleted or changes JSON
      type, no seen-question id is lost, and no seenTypes, videosSeen or lastLoc entry goes
  I2  her star total and her level never drop
  I3  every lesson she has played that the old build kept open is still open
  I4  her settings are unchanged: language, theme, sound, Hindi form, friend, prefsTouched
  I5  three more launches change nothing at all, in storage or in memory, and her next save
      changes no number and loses no field. A launch that changes a number only in memory is
      invisible to the relaunches on a phone whose boot never saves (after Start fresh), until she
      plays and it is written for her
  R   the old build reads what the new one left: the same total, level and settings, every
      played lesson open, and the full curriculum
and every launch must be free of page errors and console errors, and boot with the full curriculum.

Nothing here needs a server or the network. A static server serves hikmat/public/ under
/assets/hikmat/, which is where Frappe serves it and where the Android bundle copies it, so the
game's absolute asset paths resolve unchanged. The old build is answered from git, and every request
to any other host is refused, so a run can never reach learn.bodhya.net. --mode native also
defines window.Capacitor with fakes of Preferences, Filesystem and Share. Their shapes and error
messages follow the real @capacitor/preferences 8.0.1, filesystem 8.1.5 and share 8.0.3, and they
are backed by a Python store (Phone.NATIVE) that outlives reloads and localStorage.clear(), the
way SharedPreferences outlives a WebView that comes back empty.

Synthetic fixtures live in tests/saves/ and are committed. Real tester saves go in
tests/saves/private/, which is gitignored, and every *.json there runs too, at any depth. They are
children's data, so their output never shows a value, a count or an old student id, and an AI
coding session leaves them out unless --private says the parent's recorded OK covers it: see
tests/saves/README.md.

Adding a test: decorate `def t_name(env, report)` with @test("name"). `env.phone()` gives a fresh
phone (its own storage, and its own NATIVE store in native mode); `phone.seed(fixture)`,
`phone.launch("old"|"new")`, `phone.snap()` and `phone.page` do the rest. Report with
`report.check(cond, msg)`, and start msg with the tag `[<mode>] <name>`.
"""
import argparse
import ast
import base64
import binascii
import functools
import http.server
import json
import math
import os
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
# The data files the game fetches next to itself. Only game-old.html is answered from git, so the
# old build reads this tree's copies, which is right only while they are the ones the old ref shipped.
DATA_FILES = ("curriculum.json", "testbank.json")

CURRICULUM = json.loads((PUBLIC / "curriculum.json").read_text(encoding="utf-8"))
CURRICULUM_LEN = len(CURRICULUM)
CURRICULUM_LESSONS = {(c.get("key"), ls.get("key")) for c in CURRICULUM for ls in (c.get("lessons") or [])}
TESTBANK = json.loads((PUBLIC / "testbank.json").read_text(encoding="utf-8"))
TESTBANK_LEN = len(TESTBANK.get("bank") or []) if isinstance(TESTBANK, dict) else 0

# The fixtures --make-fixtures writes, as tests/saves/<name>.json. Nothing else belongs in that
# folder. A real save put there by mistake would run as a synthetic fixture, printing its values,
# and `git add tests/saves/` would put it in a public repo, so the harness refuses to run it and
# .gitignore ignores every other .json there. A new name here therefore also needs its own
# `!tests/saves/<name>.json` line in .gitignore, or its file is never committed.
FIXTURES = ("full-storage", "heavy", "post-erase", "pre-ledger", "retired-account", "two-accounts", "v7-midway")

BOOT_MS = 15000       # each boot promise (contentReady, nativeRestore) gets this long to settle
QUIET_MS = 500        # no request in flight for this long ends a boot, as Playwright's networkidle
HANG_S = 150          # no launch starting or finishing for this long means a page stopped answering


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


def jtype(x):
    """The JSON type of a parsed value, as the game's JSON.parse would see it."""
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "boolean"
    if isinstance(x, (int, float)):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "array"
    return "object"


def ai_session():
    """The variable that says this shell belongs to an AI coding session, or None. Claude Code
    sets CLAUDECODE=1 and AI_AGENT in every shell it opens."""
    for var in ("CLAUDECODE", "AI_AGENT"):
        if os.environ.get(var):
            return var
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
    // Her state as this launch holds it in memory. I5 compares it across launches as well,
    // because a launch that changes memory without saving leaves stateKeys exactly as it was.
    mem: JSON.parse(JSON.stringify(s)),
  };
}"""

# The first half of the boot wait: the real content is in. The bundled fallback already holds 7
# tracks and an empty test bank, so a looser test such as "more than 5 tracks" is true from the
# script's first line, long before curriculum.json arrives, and a snapshot taken then judges her
# by lessons and levels that are not loaded yet: a build that reads its content a little later
# (an IndexedDB read first, say) is reported as closing every lesson she played.
CONTENT_JS = r"""([tracks, bank]) => typeof COURSES !== "undefined" && Array.isArray(COURSES)
  && COURSES.length === tracks
  && (!bank || (typeof LEVEL_BANK !== "undefined" && !!LEVEL_BANK && Array.isArray(LEVEL_BANK.bank)
                && LEVEL_BANK.bank.length > 0))"""
CONTENT_SEEN_JS = r"""() => ({
  tracks: typeof COURSES !== "undefined" && Array.isArray(COURSES) ? COURSES.length : null,
  bank: typeof LEVEL_BANK !== "undefined" && LEVEL_BANK && Array.isArray(LEVEL_BANK.bank) ? LEVEL_BANK.bank.length : null,
})"""

# The boot promises a build may expose. v7 has neither, hence the typeof guards. A rejection is
# a bug in the build (nothing at boot may throw her progress away), so it is reported, not hidden.
# Each promise also gets `ms` to settle: page.evaluate has no timeout of its own, so one that never
# does (a nativeRestore waiting for a load event that has already fired) would otherwise hold the
# whole run open for ever. It is reported like a rejection, named BootTimeout.
BOOT_JS = r"""async (ms) => {
  const out = {};
  const bounded = (p, name) => Promise.race([p, new Promise((_, no) => setTimeout(() => {
    const e = new Error(name + " did not settle within " + ms + " ms"); e.name = "BootTimeout"; no(e);
  }, ms))]);
  const failed = e => ({ text: String((e && e.stack) || e), name: String((e && e.name) || "Error") });
  if (typeof contentReady !== "undefined") {
    try { out.contentReady = await bounded(contentReady, "contentReady"); } catch (e) { out.contentReadyError = failed(e); }
  }
  if (typeof nativeRestore !== "undefined") {
    try { out.nativeRestore = await bounded(nativeRestore, "nativeRestore"); } catch (e) { out.nativeRestoreError = failed(e); }
  }
  return out;
}"""

# Her next save, the one the next game she plays makes. It writes whatever this launch holds in
# memory, so it is the only way to see a number that boot changed in memory and never wrote.
SAVE_JS = r"""() => {
  try { save(); return { ok: true }; }
  catch (e) { return { ok: false, name: String((e && e.name) || "Error"), text: String((e && e.message) || e).slice(0, 200) }; }
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

def _is_base64(s):
    """The real plugin's test (web.js isBase64String): btoa(atob(s)) == s."""
    try:
        return base64.b64encode(base64.b64decode(s, validate=True)).decode("ascii") == s
    except (binascii.Error, ValueError):
        return False


class Native:
    """The phone's side of the Capacitor bridge, one per phone (this is the plan's NATIVE store).

    It lives in Python, so it survives reloads, relaunches and localStorage.clear(), the way
    shared_prefs/CapacitorStorage.xml survives a WebView that comes back empty. The rules copy the
    Android plugins: a missing key reads as null, a set without a key is refused with "Must provide
    key", a non-string value removes the key (PluginCall.getString() gives null and putString(key,
    null) is a remove), and Share takes only file:// urls. Filesystem.writeFile without an
    encoding takes the data as base64 and refuses anything else ("If you do not provide encoding
    and use non-base64 data, an error will be thrown", WriteFileOptions.encoding), so a save that
    forgets encoding:"utf8" fails here as it does on a phone.

    Switches a test can flip between calls:
      drop_next_write    the next Preferences.set resolves but stores nothing: a lost native write
      get_delay_ms       every Preferences.get waits this long before it reads: a slow bridge at boot
      cancel_next_share  the next Share.share is rejected with "Share canceled", as SharePlugin
                         does when she closes the share sheet without picking anything
    `log` holds every call in the order the bridge received it.
    """
    GROUP = "CapacitorStorage"

    def __init__(self):
        self.groups = {self.GROUP: {}}
        self.group = self.GROUP
        self.files = {}          # path -> {"path", "data", "directory", "encoding", "uri"}
        self.shares = []         # every Share.share() options object that was shared, in order
        self.log = []
        self.drop_next_write = False
        self.get_delay_ms = 0
        self.cancel_next_share = False

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
            if not a.get("encoding") and not _is_base64(data):
                self._note("writeFile", path=path, directory=a.get("directory"), size=len(data), refused="not base64")
                return {"__error": "The supplied data is not valid base64 content."}
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
            if self.cancel_next_share:
                self.cancel_next_share = False
                self._note("share", files=list(files or []), canceled=True)
                return {"__error": "Share canceled"}
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
        # A tester's real save is a child's data. Its checks print which check failed and where,
        # never a value, a count or an old student id, so a run's output can be shared or land in
        # a session log without carrying any of it. See tests/saves/README.md.
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
    return dict(zip(CACHE_KEYS, (compact(CURRICULUM), compact(structure), compact(TESTBANK))))


def fixture_paths():
    """(test name, path) for every generated fixture that is in tests/saves/."""
    return [("update:" + n, SAVES / (n + ".json")) for n in FIXTURES if (SAVES / (n + ".json")).is_file()]


def stray_saves():
    """The .json files in tests/saves/ that are not generated fixtures. They never run: a real save
    put there by mistake would print her values as if it were synthetic."""
    if not SAVES.is_dir():
        return []
    return sorted(p for p in SAVES.iterdir() if p.is_file() and p.suffix.lower() == ".json" and p.stem not in FIXTURES)


def private_files():
    """(saves, others) under tests/saves/private/. A save is any .json file at any depth, whatever
    the case of its name, so a file kept in a subfolder or named PHONE3.JSON is not skipped without
    a word. Anything else there is listed so the run can say it never ran; dotfiles (.DS_Store)
    are the system's own and are passed over."""
    saves, others = [], []
    if PRIVATE.is_dir():
        for p in sorted(PRIVATE.rglob("*")):
            rel = p.relative_to(PRIVATE)
            if any(part.startswith(".") for part in rel.parts) or not p.is_file():
                continue
            (saves if p.suffix.lower() == ".json" else others).append(p)
    return saves, others


def private_name(path):
    return "update:private/" + path.relative_to(PRIVATE).with_suffix("").as_posix()


def old_data_differs(ref):
    """The data files this tree serves that are not byte for byte the ones `ref` shipped."""
    out = []
    for name in DATA_FILES:
        r = subprocess.run(["git", "-C", str(ROOT), "show", "%s:hikmat/public/%s" % (ref, name)], capture_output=True)
        if r.returncode != 0 or r.stdout != (PUBLIC / name).read_bytes():
            out.append(name)
    return out


# ---------------------------------------------------------------- errors ----------------------

class Err(str):
    """One error line. `safe` is the same line with nothing from the page in it, only the kind of
    error and its type. A private save's output prints only that, because a page error or a
    console line can quote the very data it failed on (JSON.parse quotes its input)."""

    def __new__(cls, text, safe):
        s = str.__new__(cls, text)
        s.safe = safe
        return s


class BootError(Exception):
    """A launch whose content never arrived. It is a boot problem, and is reported as one, never
    as lessons that closed or a level that dropped."""


class FlowError(Exception):
    """A run through the update that crashed, carrying the page errors seen before the crash: a
    build that throws at the top of its script makes a later wait fail, and the page error is the
    reason, not the wait."""

    def __init__(self, cause, errors, private):
        if private:
            text = str(cause) if isinstance(cause, BootError) else type(cause).__name__
            errs = [getattr(e, "safe", "an error") for e in errors]
        else:
            text = "%s: %s" % (type(cause).__name__, _line(str(cause)))
            errs = [str(e) for e in errors]
        if errs:
            text += "; page errors before it: " + _clip(errs, 3)
        super().__init__(text)


class Watchdog:
    """Ends the run as a failure when no launch has started or finished for `limit` seconds.
    Every wait in a launch has a timeout, but a page whose script never yields (a retry loop that
    never stops, say) answers no evaluate at all, and Playwright's evaluate cannot time out. A gate
    that hangs is worse than one that fails, because nothing downstream ever hears why."""

    def __init__(self, limit):
        self.limit = limit
        self.label = "setup"
        self.at = None
        threading.Thread(target=self._watch, daemon=True).start()

    def kick(self, label=None):
        self.at = time.monotonic()
        if label:
            self.label = label

    def _watch(self):
        while True:
            time.sleep(1)
            if self.at is not None and time.monotonic() - self.at > self.limit:
                print("FAIL %s hung: no launch started or finished for %d s, so the run stops here"
                      % (self.label, self.limit), flush=True)
                print("\n== stopped: a page stopped answering", flush=True)
                os._exit(1)


WATCHDOG = None


def _kick(label=None):
    if WATCHDOG is not None:
        WATCHDOG.kick(label)


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

    def phone(self, sabotage=None, prelude=None, boot_ms=BOOT_MS):
        return Phone(self, sabotage, prelude, boot_ms)

    def sabotaged(self, script=None, prelude=None):
        """The new build with `script` run after the game's own scripts, and `prelude` run before
        any of them, for --self-test."""
        html = self.new_html
        if script:
            i = html.rfind("</body>")
            if i < 0:
                raise RuntimeError("game.html has no </body> to append a sabotage before")
            html = html[:i] + script + html[i:]
        if prelude:
            m = re.search(r"<head[^>]*>", html, re.I)
            if not m:
                raise RuntimeError("game.html has no <head> to put a prelude in")
            html = html[:m.end()] + "<script>" + prelude + "</script>" + html[m.end():]
        return html


class Phone:
    """One phone: its own origin storage, and in native mode its own NATIVE store."""

    def __init__(self, env, sabotage=None, prelude=None, boot_ms=BOOT_MS):
        self.env = env
        self.sabotage, self.prelude, self.boot_ms = sabotage, prelude, boot_ms
        self.errors = []         # page errors, console errors and refused requests, all launches
        self.left = []           # every url the phone tried to reach outside the local server
        self.page = None
        self.boot_result = {}
        self._answers = []
        self._expected = [API_PATH]
        self._inflight, self._net_at = 0, time.monotonic()
        self.ctx = env.browser.new_context(service_workers="block", viewport=VIEWPORT)
        self.ctx.route("**/*", self._route)
        self.NATIVE = Native() if env.mode == "native" else None
        if self.NATIVE is not None:
            self.ctx.expose_binding("__bodhyaNative", lambda source, op, args=None: self.NATIVE.call(op, args))
            self.ctx.add_init_script(NATIVE_JS)

    def _err(self, text, safe):
        self.errors.append(Err(text, safe))

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
            self._err("tried to reach %s://%s%s, and this harness is offline" % (u.scheme, u.netloc, u.path),
                      "tried to reach %s://%s, and this harness is offline" % (u.scheme, u.netloc))
            return route.abort()
        html = None
        if u.path == OLD_PATH:
            html = self.env.old_html
        elif u.path == SEED_PATH:
            html = SEED_HTML
        elif u.path == NEW_PATH and (self.sabotage or self.prelude):
            html = self.env.sabotaged(self.sabotage, self.prelude)
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
        self._err("console.error: %s%s" % (msg.text[:300], " (%s)" % url if url else ""), "console.error")

    def _on_pageerror(self, err):
        name = getattr(err, "name", None) or "Error"
        self._err("pageerror: %s: %s" % (name, getattr(err, "message", None) or err), "pageerror: %s" % name)

    def _net(self, page, delta):
        if page is self.page:
            self._inflight = max(0, self._inflight + delta)
            self._net_at = time.monotonic()

    # -- storage --------------------------------------------------------------------------
    def seed_local(self, local, with_caches=False, fill=False):
        """Make this phone's localStorage exactly `local` ({key: rawString}), on the game's own
        origin, from a page that runs no game code."""
        _kick()
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
        _kick()
        if self.page is not None:
            self.page.close()
        page = self.ctx.new_page()
        self.page = page
        self._inflight, self._net_at = 0, time.monotonic()
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("request", lambda r, p=page: self._net(p, 1))
        page.on("requestfinished", lambda r, p=page: self._net(p, -1))
        page.on("requestfailed", lambda r, p=page: self._net(p, -1))
        page.goto(self.env.base + (OLD_PATH if which == "old" else NEW_PATH))
        if wait:
            self.boot(timeout)
        _kick()
        return page

    def boot(self, timeout=20000):
        """The boot wait. First the real content: every track of curriculum.json, and the test bank,
        on which her level depends. Then contentReady and nativeRestore, when the build has them,
        each bounded so that one which never settles is a failure and not a hang. Last, no request
        in flight for QUIET_MS, because the boot callback, which saves and resumes, runs only once
        the API has answered."""
        try:
            self.page.wait_for_function(CONTENT_JS, arg=[CURRICULUM_LEN, TESTBANK_LEN > 0], timeout=timeout)
        except PWTimeout:
            seen = self.page.evaluate(CONTENT_SEEN_JS) or {}
            raise BootError("the content never loaded in %d s: COURSES has %s tracks (curriculum.json has %d), "
                            "and the test bank %s questions" % (timeout // 1000, seen.get("tracks"), CURRICULUM_LEN,
                                                                seen.get("bank")))
        self.boot_result = self.page.evaluate(BOOT_JS, self.boot_ms) or {}
        for name in ("contentReady", "nativeRestore"):
            e = self.boot_result.get(name + "Error")
            if e:
                self._err("%s rejected: %s" % (name, _line(str(e.get("text")))), "%s rejected: %s" % (name, e.get("name")))
        if not self.settle(QUIET_MS, timeout):
            self._err("the network never went idle after boot", "the network never went idle after boot")

    def settle(self, quiet_ms=QUIET_MS, timeout=20000):
        """Wait until this launch has had no request in flight for quiet_ms. Playwright's own
        networkidle fires once per navigation, so it cannot see a quiet gap that a build opens
        before its first fetch (an IndexedDB read, say), and it would call the boot over in that
        gap. This counts every request of this launch instead."""
        end = time.monotonic() + timeout / 1000.0
        while True:
            now = time.monotonic()
            if self._inflight <= 0 and now - self._net_at >= quiet_ms / 1000.0:
                return True
            if now >= end:
                return False
            self.page.wait_for_timeout(50)

    def snap(self):
        s = self.page.evaluate(SNAP_JS)
        _kick()
        return s

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


def _line(text, n=300):
    """The first line of an error, which names it; a stack trace after it would crowd out the
    page error that a crash line exists to show."""
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    return (lines[0] if lines else "")[:n]


# ---------------------------------------------------------------- the update checks -----------

LEDGERS = ("seenTypes", "videosSeen", "lastLoc")   # flag maps whose keys she has earned


def guarded(st):
    """The numbers I1 guards in one saved state, by path: every star, coins, and each level test's
    bestPct and attempts, with passed as 1 or 0. A value that is not a number (a hand-edited "3", a
    null) is left out, so it counts as neither a drop nor a gain."""
    out = {}
    if not isinstance(st, dict):
        return out
    for ck, lessons in _dict(st.get("progress")).items():
        for lk, games in _dict(lessons).items():
            for aid, v in _dict(games).items():
                n = _num(v)
                if n is not None:
                    out["progress.%s.%s.%s" % (ck, lk, aid)] = n
    n = _num(st.get("coins"))
    if n is not None:
        out["coins"] = n
    for t, rec in _dict(st.get("levelTests")).items():
        rec = _dict(rec)
        for f in ("bestPct", "attempts"):
            n = _num(rec.get(f))
            if n is not None:
                out["levelTests.%s.%s" % (t, f)] = n
        if "passed" in rec:
            out["levelTests.%s.passed" % t] = 1 if rec.get("passed") else 0
    return out


def lowered(ref, got, key, values=True):
    """Every way `got` is worse than `ref` in the numbers I1 guards, as 'key path old -> new'
    (as 'key path dropped' when values=False)."""
    if not isinstance(got, dict):
        return ["%s is no longer a saved state" % key]
    if not isinstance(ref, dict):
        return []
    have = guarded(got)
    out = []
    for path, was in guarded(ref).items():
        now = have.get(path, 0)
        if now >= was:
            continue
        if path.endswith(".passed"):
            out.append("%s %s %s" % (key, path, "turned false" if values else "dropped"))
        else:
            out.append("%s %s %s" % (key, path, "%s -> %s" % (was, now) if values else "dropped"))
    return out


def fields_lost(ref, got, key, values=True):
    """Rule 1 for one saved state: every field `ref` holds is still in `got` with the same JSON
    type, every seen-question id is still listed, and no seenTypes, videosSeen or lastLoc entry is
    gone. The numbers cannot see any of this. A wiped testSeen serves her a paper she has already
    sat, and a `lang` moved to another key makes the old build speak English to a Hindi reader."""
    if not isinstance(ref, dict) or not isinstance(got, dict):
        return []
    out = []
    for f, v in ref.items():
        if f not in got:
            out.append("%s %s deleted" % (key, f))
        elif jtype(v) != "null" and jtype(got[f]) != jtype(v):
            out.append("%s %s is now %s, not %s" % (key, f, jtype(got[f]), jtype(v)))
    have = _dict(got.get("testSeen"))
    for n, ids in _dict(ref.get("testSeen")).items():
        if not isinstance(ids, list):
            continue
        kept = have.get(n)
        kept = {json.dumps(i) for i in kept} if isinstance(kept, list) else set()
        lost = sum(1 for i in ids if json.dumps(i) not in kept)
        if lost:
            out.append("%s testSeen.%s %s" % (key, n, "lost %d of %d ids" % (lost, len(ids)) if values else "lost ids"))
    for f in LEDGERS:
        gone = [k for k in _dict(ref.get(f)) if k not in _dict(got.get(f))]
        if gone:
            out.append("%s %s lost %s" % (key, f, _clip(gone, 3) if values else "entries"))
    return out


def changed_numbers(a, b):
    """{path: (old, new)} for every guarded number that differs between two saved states."""
    ga, gb = guarded(a), guarded(b)
    paths = list(ga) + [p for p in gb if p not in ga]
    return {p: (ga.get(p, 0), gb.get(p, 0)) for p in paths if ga.get(p, 0) != gb.get(p, 0)}


def _state(x):
    return isinstance(x, dict) and "__unparseable__" not in x


def next_save(B3, B3s, saved, values=True):
    """What her next save changes in storage, from B3 (after the last relaunch) to B3s (after one
    save()). A save writes whatever the launch holds in memory, and none of her numbers or fields
    may move when it does: a change here was made at boot, in memory only, and would be written the
    moment she plays."""
    out = []
    if not (saved or {}).get("ok"):
        s = saved or {}
        out.append("her next save threw %s" % ("%s: %s" % (s.get("name"), s.get("text")) if values else s.get("name")))
    before, after = B3["stateKeys"], B3s["stateKeys"]
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if _state(a) and not _state(b):
            out.append("her next save lost %s" % key)
        if not (_state(a) and _state(b)):
            continue
        for p, (x, y) in changed_numbers(a, b).items():
            out.append("her next save changes %s %s%s" % (key, p, " %s -> %s" % (x, y) if values else ""))
        out += ["her next save: " + m for m in fields_lost(a, b, key, values)]
    return out


def key_aliases(*key_sets):
    """For a private save, hikmat_state_<id> -> hikmat_state_#n. The id is the old student id of a
    phone that was once signed in, which the README asks to be treated like a name."""
    keys = sorted({k for ks in key_sets for k in ks if k.startswith("hikmat_state_") and k != "hikmat_state_guest"})
    return {k: "hikmat_state_#%d" % (i + 1) for i, k in enumerate(keys)}


def masked(text, aliases):
    for k in sorted(aliases, key=len, reverse=True):
        text = text.replace(k, aliases[k])
    return text


def judge(fx, A, B, B3, B3s, R, saved, errors, tag, old_ref, prefix=""):
    """I1-I5, R and the error check for one run of one fixture, as [(code, ok, message)]. A is what
    v7 left, from the run through v7. B, B3, B3s and R are the run being judged, whose codes carry
    `prefix`."""
    shown = not fx.private
    aliases = {} if shown else key_aliases(fx.seed_states, *(S["stateKeys"] for S in (A, B, B3, B3s, R) if S))
    out = []

    def add(code, ok, msg):
        out.append((prefix + code, ok, masked(msg, aliases) if aliases else msg))

    probs = []
    for refs in (fx.seed_states, A["stateKeys"]):
        for key, ref in refs.items():
            got = B["stateKeys"].get(key)
            if key not in B["stateKeys"] or (_state(ref) and not _state(got)):
                probs.append("%s missing" % key)
            elif _state(ref):
                probs += lowered(ref, got, key, shown) + fields_lost(ref, got, key, shown)
    probs = list(dict.fromkeys(probs))       # the seed and A mostly agree: say each thing once
    nkeys = len(set(fx.seed_states) | set(A["stateKeys"]))
    add("I1", not probs, "%s I1 %s" % (tag, _clip(probs) if probs else
        "every star, gem, test result and seen list is still there"
        + (" (%d state keys)" % nkeys if shown else "")))

    ok = B["total"] >= A["total"] and B["level"] >= A["level"]
    add("I2", ok, "%s I2 %s" % (tag, "total %s -> %s, level %s -> %s" % (A["total"], B["total"], A["level"], B["level"])
        if shown else "total and level %s" % ("kept" if ok else "dropped")))

    lost = sorted(set(map(tuple, A["playedOpen"])) - set(map(tuple, B["playedOpen"])))
    add("I3", not lost, "%s I3 %s" % (tag, "closed by the update: " + _clip("%s/%s" % x for x in lost) if lost else
        "all %d played lessons still open" % len(A["playedOpen"]) if shown else "every played lesson still open"))

    d = diff(A["settings"], B["settings"], values=shown)
    s = B["settings"]
    add("I4", not d, "%s I4 %s" % (tag, "settings changed: " + _clip(d) if d else "settings unchanged" +
        (" (%s, %s, %s, %s)" % (s["lang"], s["theme"], s["gender"], s["mascot"]) if shown else "")))

    rel = diff(B, B3, values=shown)
    bad = (["a relaunch changed " + _clip(rel)] if rel else []) + next_save(B3, B3s, saved, shown)
    add("I5", not bad, "%s I5 %s" % (tag, _clip(bad, 4) if bad else
        "three relaunches change nothing, and her next save changes no number"))

    # R is judged against B3s, the last state the new build left, which is what the old build reads.
    rp = []
    if R["total"] != B3s["total"]:
        rp.append("total %s -> %s" % (B3s["total"], R["total"]) if shown else "total changed")
    if R["level"] != B3s["level"]:
        rp.append("level %s -> %s" % (B3s["level"], R["level"]) if shown else "level changed")
    shut = sorted(set(map(tuple, B3s["playedOpen"])) - set(map(tuple, R["playedOpen"])))
    if shut:
        rp.append("closed: " + ", ".join("%s/%s" % x for x in shut[:5]) + (" (+%d more)" % (len(shut) - 5) if len(shut) > 5 else ""))
    if R["coursesLen"] != CURRICULUM_LEN:
        rp.append("%s tracks, not the %d in curriculum.json" % (R["coursesLen"], CURRICULUM_LEN))
    d = diff(B3s["settings"], R["settings"], values=shown)
    if d:
        rp.append("settings changed: " + _clip(d))
    add("R", not rp, "%s R  %s %s" % (tag, old_ref, "rollback broke: " + _clip(rp) if rp else
        "reads it back: %s stars, level %s, the same settings, %d played lessons open, %d tracks"
        % (R["total"], R["level"], len(R["playedOpen"]), R["coursesLen"]) if shown else
        "reads it back: the same total, level and settings, every played lesson open, the full curriculum"))

    snaps = (("B", B), ("B3", B3)) if prefix else (("A", A), ("B", B), ("B3", B3))
    short = ["%s has %s" % (n, S["coursesLen"]) for n, S in snaps if S["coursesLen"] != CURRICULUM_LEN]
    errs = [str(e) if shown else getattr(e, "safe", "an error") for e in errors]
    if short:
        errs.insert(0, "the curriculum never loaded (%s tracks, not %d)" % (", ".join(short), CURRICULUM_LEN))
    add("errors", not errs, "%s %s" % (tag, _clip(errs, 3) if errs else "no page or console errors"))
    return out


def run_update(env, fx, sabotage=None, prelude=None, old_first=True):
    """One fresh phone through the update: seed it; when old_first, open the old build (A); then
    the new build (B), three relaunches (B3), her next save (B3s), and the old build again (R)."""
    ph = env.phone(sabotage=sabotage, prelude=prelude)
    try:
        try:
            ph.seed(fx)
            A = None
            if old_first:
                ph.launch("old")
                A = ph.snap()
            ph.launch("new")
            B = ph.snap()
            for _ in range(3):
                ph.launch("new")
            B3 = ph.snap()
            saved = ph.page.evaluate(SAVE_JS)
            B3s = ph.snap()
            ph.launch("old")
            R = ph.snap()
        except Exception as e:
            raise FlowError(e, ph.errors, fx.private) from e
        return A, B, B3, B3s, R, saved, list(ph.errors)
    finally:
        ph.close()


def update_results(env, fx, sabotage=None, prelude=None, direct=True):
    """Judge one fixture through the update both ways a phone meets it: through v7 first, and,
    when `direct`, straight from the seed (see the module docstring). The direct run is judged
    against A from the first."""
    tag = "[%s] %s" % (env.mode, fx.name)
    A, B, B3, B3s, R, saved, errors = run_update(env, fx, sabotage, prelude, old_first=True)
    res = judge(fx, A, B, B3, B3s, R, saved, errors, tag, env.old_ref)
    if direct:
        _, B, B3, B3s, R, saved, errors = run_update(env, fx, sabotage, prelude, old_first=False)
        res += judge(fx, A, B, B3, B3s, R, saved, errors, tag + " direct", env.old_ref, prefix="direct ")
    return res


# ---------------------------------------------------------------- tests -----------------------

class Test:
    def __init__(self, name, fn, modes=("web", "native"), once=False, private=False):
        self.name, self.fn, self.modes, self.once, self.private = name, fn, modes, once, private


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
    """The harness itself: it must stay offline, the boot wait must neither hang nor call a boot
    over before its content is in, and the native fake must behave like the real plugins, because
    the native tests later in this plan lean on all of it."""
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
        # A boot promise that never settles must be reported, not waited on for ever. The page is
        # the seed page, which runs no game code, so no build's own promises are in the way.
        pg = ph.ctx.new_page()
        try:
            pg.goto(env.base + SEED_PATH)
            pg.evaluate("() => { window.contentReady = Promise.resolve(true); window.nativeRestore = new Promise(() => {}); }")
            t0 = time.monotonic()
            r = pg.evaluate(BOOT_JS, 300) or {}
            ms = (time.monotonic() - t0) * 1000
        finally:
            pg.close()
        e = r.get("nativeRestoreError") or {}
        report.check(r.get("contentReady") is True and e.get("name") == "BootTimeout"
                     and "did not settle" in e.get("text", "") and ms < 3000,
                     "%s a nativeRestore that never settles is reported as BootTimeout, not waited on" % tag)
        # Content that never arrives is a boot failure that says so, not a progress drop.
        slow = env.phone()
        try:
            slow.seed_local({})
            slow.answer(r"^/assets/hikmat/curriculum\.json$", status=404)
            try:
                slow.launch("new", timeout=2500)
                why = "booted"
            except BootError as err:
                why = str(err)
        finally:
            slow.close()
        report.check("never loaded" in why and "curriculum.json has %d" % CURRICULUM_LEN in why,
                     "%s a launch whose curriculum never arrives fails the boot wait, naming it" % tag)
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
              try { await F.writeFile({ path: "plain.json", data: '{"kind":"bodhya-progress"}', directory: "CACHE" }); out.plain = "resolved"; }
              catch (e) { out.plain = e.message; }
              out.b64 = await F.writeFile({ path: "b64.bin", data: btoa("{}"), directory: "CACHE" });
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
            report.check(r["plain"] == "The supplied data is not valid base64 content." and "plain.json" not in N.files
                         and r["b64"] == {"uri": "file:///cache/b64.bin"},
                         "%s fake Filesystem.writeFile refuses plain text with no encoding, as the plugin does" % tag)
            report.check(r["share"] == {"activityType": ""} and N.shares[-1].get("files") == ["file:///cache/probe.json"]
                         and r["emptyShare"] == "Must provide a URL or Message or files",
                         "%s fake Share.share records the call and refuses an empty share" % tag)
            N.cancel_next_share = True
            r = ph.page.evaluate("""async () => { const S = Capacitor.Plugins.Share, out = {};
                                     try { await S.share({ title: 'probe', files: ['file:///cache/probe.json'] }); out.first = 'resolved'; }
                                     catch (e) { out.first = e.message; }
                                     out.second = await S.share({ title: 'probe', files: ['file:///cache/probe.json'] });
                                     return out; }""")
            report.check(r["first"] == "Share canceled" and r["second"] == {"activityType": ""} and len(N.shares) == 2,
                         "%s cancel_next_share rejects exactly the next share with 'Share canceled'" % tag)
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


def _sab(note, js):
    return "<script>/* e2e self-test: %s */(function(){%s})();</script>" % (note, js)


# A correct build, not a broken one: every fetch starts 700 ms late, as when a build reads its
# content store before it fetches. Nothing is lost, so every check must pass.
SLOW_FETCH = ("/* e2e self-test: content arrives late */(function(){var f=window.fetch;window.fetch=function(){"
              "var a=arguments;return new Promise(function(r){setTimeout(r,700);}).then(function(){return f.apply(window,a);});};})();")
# The new build's own account fold keeps hikmat_current after it worked, so every launch folds the
# old account in again. Only a phone that never ran v7 can show it: v7 has already removed the key.
KEEP_CURRENT = ("/* e2e self-test: hikmat_current survives the fold */(function(){var rm=Storage.prototype.removeItem;"
                "Storage.prototype.removeItem=function(k){if(k==='hikmat_current')return;return rm.apply(this,arguments);};})();")


def _private_output_problems():
    """Judge a private save twice, once where every check passes and once where every check
    fails, and crash one run, then return every line that shows an old student id, one of her
    values or counts, or text a page error quoted. "->" appears only where a value is printed.
    Needs no browser."""
    seed_s = {"progress": {"bazaar": {"l01": {"learn": 3}, "l02": {"listen": 2}}}, "coins": 98765,
              "testSeen": {"L1": ["q1"]}, "lang": "hi"}
    seed_g = {"progress": {}, "coins": 4321}
    fx = Fixture("private/phone-2026-11-05", {"kind": "storage-snapshot", "note": "x", "localStorage": {
        "hikmat_state_S-0042": compact(seed_s), "hikmat_state_guest": compact(seed_g)}}, private=True)
    tag = "[web] private/phone-2026-11-05"

    def snap(keys, total, level, lang, lessons, tracks=CURRICULUM_LEN):
        return {"stateKeys": keys, "total": total, "level": level, "playedOpen": lessons, "coursesLen": tracks,
                "mem": {"coins": total}, "settings": {"lang": lang, "theme": "dark", "sound": True, "gender": "Female",
                                                      "mascot": "tara", "prefsTouched": True}}
    played = [["bazaar", "l01"], ["bazaar", "l02"]]
    A = snap({"hikmat_state_S-0042": seed_s, "hikmat_state_guest": seed_g}, 777, 3, "hi", played)
    good = judge(fx, A, A, A, A, A, {"ok": True}, [], tag, "play-v7")
    B = snap({"hikmat_state_guest": {"coins": 1}}, 555, 2, "en", [], tracks=7)
    B3 = snap({"hikmat_state_guest": {"coins": 2}, "hikmat_state_S-0042": {"coins": 3}}, 556, 2, "en", played)
    R = snap({}, 554, 1, "hi", [], tracks=7)
    errs = [Err("pageerror: SyntaxError: \"Rekha Kumari, Class 6\" is not valid JSON", "pageerror: SyntaxError")]
    bad = judge(fx, A, B, B3, B3, R, {"ok": False, "name": "QuotaExceededError", "text": "Rekha Kumari"}, errs, tag, "play-v7")
    lines = [m[len(tag):] for _c, _ok, m in good + bad] + [str(FlowError(RuntimeError("Rekha Kumari"), errs, private=True))]
    leaks = ("S-0042", "98765", "4321", "777", "555", "556", "554", "Rekha", "->", "tara", "hi,",
             "state keys", "stars", "played lessons", "level 3", "level 2")
    out = [m for m in lines if any(x in m for x in leaks)]
    if not all(ok for _c, ok, _m in good) or any(ok for _c, ok, _m in bad) or len(good) != 7 or len(bad) != 7:
        out.append("the probe did not pass and fail as built")
    return out


@test("self-test", once=True)
def t_self_test(env, report):
    """The checks must be able to fail, each on its own. Broken builds go through the same update
    as the fixtures, and each must fail exactly the check it breaks, naming what it broke. A build
    that is slow but correct must still pass, and a private save's output must carry nothing of hers."""
    tag = "[%s] self-test" % env.mode
    leaks = _private_output_problems()
    report.check(not leaks, "%s a private save's failures show no value, count, old student id or page text%s"
                 % (tag, (": " + _clip(leaks, 3)) if leaks else ""))

    mid = load_fixture(SAVES / "v7-midway.json", "v7-midway (sabotaged)")
    ck, lk, aid = _played(mid)

    def expect(what, fx, codes, named, script=None, prelude=None, direct=False):
        res = update_results(env, fx, sabotage=script, prelude=prelude, direct=direct)
        failed = {code: msg for code, ok, msg in res if not ok}
        for msg in failed.values():
            print("    caught: " + msg)
        ok = set(failed) == set(codes) and all(text in failed.get(code, "") for code, text in named)
        said = ("fails %s and nothing else, naming %s" % (" and ".join(codes), ", ".join(t for _c, t in named))
                if codes else "passes every check, because nothing was lost")
        report.check(ok, "%s %s %s%s" % (tag, what, said, "" if ok else " (failed: %s)" % (", ".join(sorted(failed)) or "nothing")))

    q = json.dumps
    expect("a star set to 0", mid, ["I1", "I2"], [("I1", "%s.%s.%s" % (ck, lk, aid))], script=_sab(
        "one played star is lost", "var c=%s,l=%s,a=%s;var g=((state.progress||{})[c]||{})[l];if(g&&g[a]){g[a]=0;save();}"
        % (q(ck), q(lk), q(aid))))
    expect("a played lesson shut by lessonLock", mid, ["I3"], [("I3", "%s/%s" % (ck, lk))], script=_sab(
        "one played lesson is shut", "var c=%s,l=%s,orig=lessonLock;lessonLock=function(course,ls){"
        "if(course&&ls&&course.key===c&&ls.key===l)return{kind:'level',level:5};return orig.apply(this,arguments);};"
        % (q(ck), q(lk))))
    expect("a theme the build changes by itself", mid, ["I4"], [("I4", "theme")],
           script=_sab("a setting changes", "state.theme='light';save();"))
    expect("an attempt added at every launch", mid, ["I5"], [("I5", "levelTests.1.attempts")],
           script=_sab("a launch inflates attempts", "state.levelTests['1'].attempts+=1;save();"))
    expect("gems added in memory only, after Start fresh", load_fixture(SAVES / "post-erase.json", "post-erase (sabotaged)"),
           ["I5"], [("I5", "her next save changes hikmat_state_guest coins")],
           script=_sab("gems grow in memory only", "state.coins+=5;"))
    expect("a star total the new build counts one higher", mid, ["R"], [("R", "total")], script=_sab(
        "the total is miscounted", "var t=totalStars;totalStars=function(){return t.apply(this,arguments)+1;};"))
    expect("a theme stored differently from the one it shows", mid, ["R"], [("R", "theme")], script=_sab(
        "a setting moves out of the stored state", "var real=save;state.theme='dark';save=function(){var t=state.theme;"
        "state.theme='light';try{return real.apply(this,arguments);}finally{state.theme=t;}};save();"))
    expect("wiped seen-question lists", mid, ["I1"], [("I1", "testSeen.L1 lost")],
           script=_sab("a field loses its contents", "state.testSeen={};save();"))
    expect("gems and test results v7 folded in, lowered", load_fixture(SAVES / "two-accounts.json", "two-accounts (sabotaged)"),
           ["I1"], [("I1", "hikmat_state_guest coins 300 -> 0")],
           script=_sab("what v7 left is lowered", "state.coins=0;state.levelTests={};state.testSeen={};save();"))
    expect("hikmat_current kept after the new build's fold", load_fixture(SAVES / "retired-account.json", "retired-account (sabotaged)"),
           ["direct I5"], [("direct I5", "levelTests.1.attempts")], prelude=KEEP_CURRENT, direct=True)
    expect("content that arrives 700 ms late", mid, [], [], prelude=SLOW_FETCH)


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

    # post-erase: the v7-midway girl just after Start fresh in v7. v7's #progWipe handler builds
    # Object.assign(fresh(), keep), keeping lang, sound, theme, gender, mascot and prefsTouched and
    # nothing else, so the keys fall in exactly this order and lastLoc and lastMode are gone. With
    # prefsTouched set and no lastMode, neither boot save runs (applyServerDefaults, autoResume),
    # so this is the phone where a number changed only in memory goes unseen by the relaunches.
    erased = {"progress": {}, "coins": 0, "hindi": False, "lang": midway["lang"], "sound": midway["sound"],
              "testSeen": {}, "levelTests": {}, "videosSeen": {}, "seenTypes": {},
              "theme": midway["theme"], "gender": midway["gender"], "mascot": midway["mascot"],
              "prefsTouched": midway["prefsTouched"]}

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
        "post-erase": envelope(erased),
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
    docs = build_fixtures(cat)
    if set(docs) != set(FIXTURES):
        raise SystemExit("build_fixtures() and FIXTURES name different fixtures: %s" % sorted(set(docs) ^ set(FIXTURES)))
    SAVES.mkdir(parents=True, exist_ok=True)
    for name in sorted(docs):
        doc = docs[name]
        path = SAVES / (name + ".json")
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        fx = Fixture(name, doc)
        stars = sum(_num(v) or 0 for st in fx.seed_states.values() for c in _dict(_dict(st).get("progress")).values()
                    for l in _dict(c).values() for v in _dict(l).values())
        lessons = sum(len(_dict(c)) for st in fx.seed_states.values() for c in _dict(_dict(st).get("progress")).values())
        print("wrote %s (%d state keys, %d lessons, %d stars)" % (path.relative_to(ROOT), len(fx.seed_states), lessons, stars))


# ---------------------------------------------------------------- main ------------------------

def setup_checks(report, old_ref, agent):
    """The premises every result rests on, checked before any phone starts."""
    differ = old_data_differs(old_ref)
    report.check(not differ, "[setup] %s" % (
        "%s in this tree %s not the %s %s shipped, and only game-old.html is answered from git, so the old build "
        "here would read content it never had, and R would count tracks it never showed. Serve old launches their "
        "own copies from git before trusting R." % (" and ".join(differ), _n(len(differ), "is", "are"),
                                                   _n(len(differ), "one", "ones"), old_ref) if differ else
        "%s shipped the curriculum.json and testbank.json this tree serves, so the old build reads only what it had"
        % old_ref))
    missing = [n + ".json" for n in FIXTURES if not (SAVES / (n + ".json")).is_file()]
    if missing:
        report.check(False, "[setup] tests/saves/ is missing %s: run --make-fixtures" % ", ".join(missing))
    # File names are shown only outside an AI session: someone may have named a file after the girl.
    stray = stray_saves()
    if stray:
        n = len(stray)
        report.check(False, "[setup] tests/saves/ holds %d .json %s not a generated fixture, so %s not run%s. "
                     "A tester's save goes in tests/saves/private/." % (
                         n, _n(n, "file that is", "files that are"), _n(n, "it does", "they do"),
                         "" if agent else " (%s)" % ", ".join(p.name for p in stray)))
    _saves, others = private_files()
    if others:
        n = len(others)
        report.check(False, "[setup] tests/saves/private/ holds %d %s not a .json save, so %s not run%s. "
                     "Rename or remove %s." % (
                         n, _n(n, "file that is", "files that are"), _n(n, "it does", "they do"),
                         "" if agent else " (%s)" % ", ".join(p.relative_to(PRIVATE).as_posix() for p in others),
                         _n(n, "it", "them")))


def _n(n, one, many):
    return one if n == 1 else many


def main():
    global WATCHDOG
    ap = argparse.ArgumentParser(description="Replay saved progress through the update and back.")
    ap.add_argument("--old", default="play-v7", help="git ref of the build she has now (default play-v7)")
    ap.add_argument("--mode", choices=("web", "native", "both"), default="both")
    ap.add_argument("--only", help="run only tests whose name contains this")
    ap.add_argument("--self-test", action="store_true", help="only prove that the checks can fail")
    ap.add_argument("--make-fixtures", action="store_true", help="rebuild tests/saves/*.json from --old")
    ap.add_argument("--private", dest="private", action="store_true", default=None,
                    help="run the tester saves in tests/saves/private/ even in an AI session; only where the "
                         "parent's recorded OK covers it")
    ap.add_argument("--no-private", dest="private", action="store_false",
                    help="leave the tester saves in tests/saves/private/ out")
    args = ap.parse_args()

    try:
        old_html = subprocess.run(["git", "-C", str(ROOT), "show", "%s:hikmat/public/game.html" % args.old],
                                  check=True, capture_output=True).stdout.decode("utf-8")
    except subprocess.CalledProcessError as e:
        sys.exit("cannot read hikmat/public/game.html at %s: %s" % (args.old, e.stderr.decode().strip()))
    new_html = (PUBLIC / "game.html").read_text(encoding="utf-8")

    if args.make_fixtures:
        differ = old_data_differs(args.old)
        if differ:
            sys.exit("%s in this tree differs from %s's, so fixtures built now would use content that build never "
                     "had" % (" and ".join(differ), args.old))
    agent = ai_session()
    run_private = args.private if args.private is not None else agent is None
    saves, _others = private_files()

    tests = [Test(name, t_update(path, name)) for name, path in fixture_paths()]
    if run_private:
        tests += [Test(private_name(p), t_update(p, private_name(p)), private=True) for p in saves]
    tests += TESTS
    if args.self_test:
        tests = [t for t in tests if t.name == "self-test"]
    elif args.only:
        tests = [t for t in tests if args.only == t.name or args.only in t.name]
    modes = ["web", "native"] if args.mode == "both" else [args.mode]

    srv = start_server()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    report = Report()
    t0 = time.time()
    WATCHDOG = Watchdog(HANG_S)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                if args.make_fixtures:
                    make_fixtures(Env(browser, base, "web", args.old, old_html, new_html))
                    return 0
                setup_checks(report, args.old, agent)
                if saves and not run_private:
                    n = len(saves)
                    print("-- %d tester %s in tests/saves/private/ left out (--no-private)." % (n, _n(n, "save", "saves"))
                          if args.private is False else
                          "-- %d tester %s in tests/saves/private/ left out: this is an AI session (%s is set). Run %s "
                          "in a plain terminal, or pass --private where the parent's recorded OK covers this session."
                          % (n, _n(n, "save", "saves"), agent, _n(n, "it", "them")), flush=True)
                if not tests:
                    report.check(False, "no test matches --only %r" % args.only)
                for mode in modes:
                    env = Env(browser, base, mode, args.old, old_html, new_html)
                    for t in tests:
                        if mode not in t.modes or (t.once and mode != modes[0]):
                            continue
                        print("\n-- [%s] %s" % (mode, t.name), flush=True)
                        _kick("[%s] %s" % (mode, t.name))
                        try:
                            t.fn(env, report)
                        except Exception as e:
                            if isinstance(e, FlowError):
                                text = str(e)
                            elif t.private:
                                text = type(e).__name__
                            else:
                                text = "%s: %s" % (type(e).__name__, str(e)[:300])
                            report.check(False, "[%s] %s crashed: %s" % (mode, t.name, text))
            finally:
                browser.close()
    finally:
        srv.shutdown()
    ran = sum(1 for t in tests if t.private)
    left = 0 if run_private else len(saves)
    print("\n== %d passed, %d failed (%s, old=%s, %d tester %s run%s, %.0fs)" % (
        report.passed, len(report.fails), "+".join(modes), args.old, ran, _n(ran, "save", "saves"),
        ", %d left out" % left if left else "", time.time() - t0))
    print("FAILS", len(report.fails), report.fails)
    return 1 if report.fails else 0


if __name__ == "__main__":
    sys.exit(main())
