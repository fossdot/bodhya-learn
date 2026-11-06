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
      plays and it is written for her. A next save that localStorage refused wrote nothing, so it
      fails I5 too: the comparison after it would prove nothing
  R   the old build reads what the new one left: the same total, level and settings, every
      played lesson open, and the full curriculum
and every launch must be free of page errors and console errors, and boot with the full curriculum.

Task 2 adds seven tests of a full localStorage (rule 4: guard every write), each on a fresh phone:
  S1  storage_full_drops_caches   full, with the three content caches in it: a new star is still
      kept, because the save drops those caches and writes again. Nothing else goes to make room,
      not even the settings cache such a phone also holds
  S2  storage_full_no_caches      full, with nothing in it that may go: save() returns false
      instead of throwing, nothing is removed, and a lesson still plays to its result screen
  S3  retire_under_full_storage   an old signed-in account on a phone full to the last character,
      with nothing that may go: the account fold cannot land, so hikmat_current stays for the next
      launch and nothing is deleted, and once there is room the fold lands, counting each attempt once
  S4  storage_full_live_content   the S1 phone online: caching the server's lessons cannot throw,
      so the settings it sent are applied and the server counts as reached
  S5  erase_while_fold_pending    the S3 phone, and she taps Start fresh while the fold still
      waits: the erase ends the fold too, so no launch with room brings the account's stars back,
      and it keeps the account's guide and Hindi form, which only hikmat_current held. The profile
      it leaves is the one a phone leaves where the fold landed first and she then erased
  S6  retire_frees_queue_room     an old signed-in account on a phone full of its own unsent queue:
      the queue goes before the fold is written, so the fold lands in its room on the first launch
  S7  erase_refused_while_fold_pending   the S5 phone with no guest profile, so localStorage
      refuses the erase itself: hikmat_current stays until a save that holds its guide and Hindi
      form has landed, and the fold ends with that save

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
# `value` is what save() returned: false from a build that guards its writes means localStorage
# refused it, so nothing was written and the comparison after it would prove nothing. v7's save
# returns nothing at all.
SAVE_JS = r"""() => {
  try { return { ok: true, value: save() }; }
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
    set: async o => {
      const b = await call("Preferences.set:begin", o);
      if (b.delay > 0) await wait(b.delay);
      await call("Preferences.set", o);
    },
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
      drop_next_write    the next Preferences.set resolves but stores nothing: a lost native write.
                         A key name instead of True loses the next set of that key only, so a
                         test can lose the erase's own write and not the undo copy written first
      get_delay_ms       every Preferences.get waits this long before it reads: a slow bridge at boot
      fail_gets          this many Preferences.get calls are rejected ("bridge read failed"), then
                         reads work again: a bridge that could not answer at boot
      slow_next_write    (key, ms): the next Preferences.set of that key waits ms before it lands,
                         and later writes queue behind it, as they do on the plugin's thread
      refuse_writes      while true, every Preferences.set is rejected: a phone whose disk is full
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
        self.fail_gets = 0
        self.slow_next_write = None
        self.refuse_writes = False
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
        if op in ("Preferences.get:begin", "Preferences.get", "Preferences.set:begin", "Preferences.set",
                  "Preferences.remove") and not isinstance(key, str):
            return {"__error": "Must provide key"}
        if op == "Preferences.configure":
            g = a.get("group")
            self.group = g if isinstance(g, str) and g else self.GROUP
            self._note("configure", group=self.group)
            return {}
        if op == "Preferences.get:begin":
            if self.fail_gets > 0:
                self.fail_gets -= 1
                self._note("get:begin", key=key, failed=True)
                return {"__error": "bridge read failed"}
            self._note("get:begin", key=key)
            return {"delay": max(0, int(self.get_delay_ms or 0))}
        if op == "Preferences.set:begin":
            slow = self.slow_next_write
            if slow and slow[0] == key:
                self.slow_next_write = None
                self._note("set:begin", key=key, delay=int(slow[1]))
                return {"delay": int(slow[1])}
            return {"delay": 0}
        if op == "Preferences.get":
            v = self.prefs.get(key)
            self._note("get", key=key, found=v is not None)
            return {"value": v}
        if op == "Preferences.set":
            v = a.get("value") if isinstance(a.get("value"), str) else None
            if self.refuse_writes:
                self._note("set", key=key, size=len(v or ""), refused=True)
                return {"__error": "Unable to write to storage"}
            if self.drop_next_write is True or (self.drop_next_write and self.drop_next_write == key):
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
    s = saved or {}
    if not s.get("ok"):
        out.append("her next save threw %s" % ("%s: %s" % (s.get("name"), s.get("text")) if values else s.get("name")))
    elif s.get("value") is False:
        out.append("her next save was refused, so nothing was written and this check proved nothing")
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
                         and "harness_probe" in (r["keys"] or {}).get("keys", [])  # the app's mirror keys are there too
                         and r["noKey"] == "Must provide key",
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


# ---------------------------------------------------------------- a full phone (Task 2) -------
#
# Rule 4: a full localStorage must never cost her a star. Each test below fills a phone and
# returns [(code, ok, message)] instead of printing, so that the self-test can run it on a broken
# build and see it fail.

# What a full phone holds, read from storage itself and not through the game: the raw value of
# every saved state and of hikmat_current, and the length of everything else. A full phone holds
# megabytes of filler and cache, and these tests only need to know that those keys are still
# there at the same size. `full` says whether even one more short key would fit.
STORAGE_JS = r"""() => {
  const keys = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i), v = localStorage.getItem(k);
    keys[k] = (k.indexOf("hikmat_state_") === 0 || k === "hikmat_current") ? v : v.length;
  }
  let full = false;
  try { localStorage.setItem("zz_probe_full", "z"); localStorage.removeItem("zz_probe_full"); } catch (e) { full = true; }
  return { keys, full };
}"""

# A star she has never had, then her save, which is what finish() does at the end of a game. On a
# full phone the new star makes her saved state longer, so the write fits only if something makes
# room for it.
STAR_SAVE_JS = r"""([ck, lk]) => {
  setStars(ck, lk, "learn", 3);
  try { return { ok: true, value: save() }; }
  catch (e) { return { ok: false, name: String((e && e.name) || "Error"), text: String((e && e.message) || e).slice(0, 200) }; }
}"""

# Her tap on a lesson in its track: the lesson's map, with its first game ready to start.
OPEN_LESSON_JS = r"""([ck, lk]) => {
  const c = COURSES.find(x => x.key === ck), ls = c && (c.lessons || []).find(l => l.key === lk);
  if (!ls) return "the curriculum has no lesson " + ck + "/" + lk;
  try { renderLessonMap(c, ls); return ""; }
  catch (e) { return "opening the lesson threw " + String((e && e.name) || "Error") + ": " + String((e && e.message) || e).slice(0, 160); }
}"""

RESULT_JS = r"""() => { const r = document.querySelector(".screen.result .earn"); return r ? r.textContent.trim() : null; }"""

DROP_FILLER_JS = r"""() => {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const k = localStorage.key(i);
    if (k && k.indexOf("zz_filler_") === 0) localStorage.removeItem(k);
  }
}"""


def _fresh_lessons(fx, n):
    """The first n published lessons, in curriculum order, that have a Learn game (they have
    words) and that the fixture's guest state has never played. A star there is new, so saving it
    makes her state longer."""
    prog = _dict(_dict(fx.seed_states.get("hikmat_state_guest")).get("progress"))
    out = []
    for c in CURRICULUM:
        if not c.get("published"):
            continue
        for ls in c.get("lessons") or []:
            if ls.get("words") and not _dict(prog.get(c.get("key"))).get(ls.get("key")):
                out.append((c["key"], ls["key"]))
                if len(out) == n:
                    return out
    raise RuntimeError("the curriculum has fewer than %d lessons with words that %s has not played" % (n, fx.name))


def _parsed(raw):
    """A stored state as the game would parse it, or None when it is missing or unreadable."""
    if not isinstance(raw, str):
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _learn_star(st, ck, lk):
    return _num(_dict(_dict(_dict(_dict(st).get("progress")).get(ck)).get(lk)).get("learn")) or 0


def play_learn(page, ck, lk, taps=12):
    """Play the Learn game of one lesson, from her tap on the lesson to the result screen, with
    the game's own buttons. Returns what went wrong, or "" when the result screen came up with
    three stars. A save that throws inside a tap handler shows up twice: as a page error, and as a
    game that never reaches its result screen, because the throw stops the round where it is."""
    err = page.evaluate(OPEN_LESSON_JS, [ck, lk])
    if err:
        return err
    try:
        page.click("#path .step.current .node", timeout=3000)
        for _ in range(taps):
            if page.locator(".screen.result").count():
                break
            page.click("#go" if page.locator("#go").count() else "#footer #next", timeout=3000)
    except PWTimeout as e:
        return "the game stopped answering her taps (%s)" % _line(str(e), 120)
    shown = page.evaluate(RESULT_JS)
    if shown is None:
        return "the game never reached its result screen"
    if shown != "★★★":
        return "the result screen shows %s, not three stars" % shown
    return ""


def _keys(keys, n=3):
    """A short list of storage keys for a message."""
    keys = list(keys)
    return ", ".join(keys[:n]) + (" (+%d more)" % (len(keys) - n) if len(keys) > n else "")


def _left_alone(before, after, may_go=(), may_change=()):
    """Every key that was in `before` and is not a saved state or in `may_go`, removed or changed
    in `after`. A key in `may_change` may be rewritten, but it must still be there. Only the three
    content caches may ever be removed to make room, so a filler key that goes is a write that
    made room by throwing away something that was not its to drop."""
    gone, changed = [], []
    for k, v in sorted(before.items()):
        if k in may_go or k.startswith("hikmat_state_"):
            continue
        if k not in after:
            gone.append(k)
        elif after[k] != v and k not in may_change:
            changed.append(k)
    out = []
    if gone:
        out.append("removed to make room: " + _keys(gone))
    if changed:
        out.append("changed: " + _keys(changed))
    return out


def _seeded(ph):
    """STORAGE_JS on the phone as it was seeded, read from the seed page before any build has run.
    A boot that makes room by deleting something has already done it by the time the game's page
    can be asked, so only this can show what was really there."""
    pg = ph.ctx.new_page()
    try:
        pg.goto(ph.env.base + SEED_PATH)
        return pg.evaluate(STORAGE_JS)
    finally:
        pg.close()


# What a phone that has been online holds besides the three content caches: the server's
# settings, shaped as get_settings() sends them (_build_settings in hikmat/api.py). The game
# caches them with a small write of its own, and Task 3 keeps them in localStorage, so no write may
# drop them to make room.
SETTINGS_CACHE = compact({"appName": "Bodhya Learn", "logo": "", "taglineEn": "Learn by playing",
                          "taglineHi": "खेलकर सीखो", "defaultLanguage": "en", "helpDefaultOn": False,
                          "defaultTheme": "light", "defaultSound": True, "socialLogins": [], "aiEnabled": False,
                          "voiceEnabled": False, "otpEnabled": False})


def _states_kept(fx, after):
    """Every saved state the fixture seeded is still in `after`, with nothing in it lowered or lost."""
    out = []
    for key, ref in sorted(fx.seed_states.items()):
        got = _parsed(after.get(key))
        if got is None:
            out.append("%s missing" % key)
        elif isinstance(ref, dict):
            out += lowered(ref, got, key) + fields_lost(ref, got, key)
    return out


def _full_phone_run(ph, steps):
    """Run `steps(ph, probs)` on a phone and gather what it found, its crash and the page's errors
    into one list. A crash fails this one check and not the whole run, so the self-test can see it."""
    probs = []
    try:
        steps(ph, probs)
        if ph.errors:
            probs.append(_clip(ph.errors, 3))
    except Exception as e:
        probs.append("crashed: %s" % FlowError(e, ph.errors, False))
    finally:
        ph.close()
    return probs


def storage_full_drops_caches(env, sabotage=None, prelude=None):
    """S1. A phone full to the last byte, with the three content caches in it, as a phone that has
    been online for months is. She earns a new star: the save must keep it, by dropping the caches
    (the game rebuilds them from its bundled files, and from the server when it answers) and
    writing again. Nothing else may go: every saved state and every other key stays as it was,
    the settings cache such a phone also holds among them.

    Since Task 3 a launch moves the caches into IndexedDB (C1), and that alone makes room, so the
    caches can still be in localStorage at her next save only where IndexedDB is not there to take
    them. That is the phone this runs on: IndexedDB refuses to open, as in C3."""
    tag, code = "[%s] storage_full_drops_caches" % env.mode, "S1"
    prelude = IDB_BLOCKED + (prelude or "")
    fx = load_fixture(SAVES / "full-storage.json", "full-storage")
    fx.local = dict(fx.local, hikmat_settings_cache=SETTINGS_CACHE)
    if env.mode == "native":
        # In the app every state this build writes carries nativeCopy, so its first save over a
        # state v7 left is 18 bytes longer. On a phone full to the last byte that boot save is the
        # one that drops the caches, as it may. The save this check is about is hers, later, so in
        # the app the phone is one this build has already saved on. The first launch on a full v7
        # phone is the full-storage fixture's own update run.
        marked = dict(fx.seed_states[GUEST], nativeCopy=True)
        fx.local[GUEST], fx.seed_states[GUEST] = compact(marked), marked
    (ck, lk), = _fresh_lessons(fx, 1)

    def steps(ph, probs):
        ph.seed(fx)
        seeded = _seeded(ph)["keys"]
        ph.launch("new")
        before = ph.page.evaluate(STORAGE_JS)
        if not before["full"]:
            probs.append("the phone was not full before the save, so the save proved nothing")
        absent = [k for k in CACHE_KEYS if k not in before["keys"]]
        if absent:
            probs.append("%s not in storage before the save, so there was nothing to drop" % _keys(absent))
        r = ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        if not r["ok"]:
            probs.append("save() threw %s: %s" % (r["name"], _line(r["text"], 120)))
        elif r["value"] is not True:
            probs.append("save() returned %s, not true" % json.dumps(r["value"]))
        ph.launch("new")
        after = ph.page.evaluate(STORAGE_JS)["keys"]
        if _learn_star(_parsed(after.get("hikmat_state_guest")), ck, lk) != 3:
            probs.append("after a reload the star at %s/%s/learn is not in hikmat_state_guest" % (ck, lk))
        if ph.page.evaluate("([ck, lk]) => getStars(ck, lk, 'learn')", [ck, lk]) != 3:
            probs.append("after a reload the game does not show the star at %s/%s/learn" % (ck, lk))
        kept = [k for k in CACHE_KEYS if k in after]
        if kept:
            probs.append("%s still in localStorage" % _keys(kept))
        # Against the phone as seeded, so that a key the boot already threw away counts too.
        probs.extend(_states_kept(fx, after) + _left_alone(seeded, after, may_go=CACHE_KEYS))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "the new star at %s/%s survived a reload: the save dropped the three content caches and nothing else, "
             "and every saved state is intact" % (ck, lk)))]


def storage_full_no_caches(env, sabotage=None, prelude=None):
    """S2. Full, with nothing in storage that may go: her own state and filler, no caches. The save
    cannot be kept, and it must say so by returning false instead of throwing, and she must still
    be able to play. A throw here stops the game she is in: the star she is earning is lost, and
    so is every one after it until the app is closed."""
    tag, code = "[%s] storage_full_no_caches" % env.mode, "S2"
    fx = load_fixture(SAVES / "full-storage.json", "full-storage without caches")
    fx.with_caches = False
    (ck, lk), (pk, pl) = _fresh_lessons(fx, 2)

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        before = ph.page.evaluate(STORAGE_JS)
        if not before["full"]:
            probs.append("the phone was not full before the save, so the save proved nothing")
        r = ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        if not r["ok"]:
            probs.append("save() threw %s: %s" % (r["name"], _line(r["text"], 120)))
        elif r["value"] is not False:
            probs.append("save() returned %s, not false, with nothing it may drop" % json.dumps(r["value"]))
        why = play_learn(ph.page, pk, pl)
        if why:
            probs.append("the lesson %s/%s did not play: %s" % (pk, pl, why))
        elif ph.page.evaluate("([ck, lk]) => getStars(ck, lk, 'learn')", [pk, pl]) != 3:
            probs.append("the three stars she just earned at %s/%s are not in the game's state" % (pk, pl))
        after = ph.page.evaluate(STORAGE_JS)["keys"]
        probs.extend(_states_kept(fx, after) + _left_alone(before["keys"], after))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "save() returned false without throwing, nothing was removed, and %s/%s played to three stars"
             % (pk, pl)))]


ACCOUNT_KEY = "hikmat_state_S-0042"      # the retired-account fixture's signed-in learner


def _starred(st):
    """{path: stars} for every game with a star in one saved state."""
    return {p: v for p, v in guarded(st).items() if p.startswith("progress.") and v > 0}


def _fold_landed(fx, keys):
    """What is wrong with hikmat_state_guest in `keys` (STORAGE_JS) as the retired-account fold,
    landed: every star, gem and test result of S-0042's account and of the old guest profile, every
    field the guest profile had, and each level test's attempts the two added once, since the guest
    profile had never taken this fold before."""
    acct, guest0 = fx.seed_states[ACCOUNT_KEY], fx.seed_states["hikmat_state_guest"]
    guest = _parsed(keys.get("hikmat_state_guest"))
    if guest is None:
        return ["hikmat_state_guest missing"]
    out = (lowered(acct, guest, "the guest profile, against %s," % ACCOUNT_KEY)
           + lowered(guest0, guest, "hikmat_state_guest") + fields_lost(guest0, guest, "hikmat_state_guest"))
    for n, rec in sorted(_dict(acct.get("levelTests")).items()):
        want = (_num(_dict(_dict(guest0.get("levelTests")).get(n)).get("attempts")) or 0) + (_num(_dict(rec).get("attempts")) or 0)
        got = _num(_dict(_dict(guest.get("levelTests")).get(n)).get("attempts"))
        if got != want:
            out.append("levelTests.%s.attempts is %s, not %s: the fold did not count each attempt once" % (n, got, want))
    return out


def _worth(fx):
    """'N stars in M games' for S-0042's account, for a message."""
    stars = _starred(fx.seed_states[ACCOUNT_KEY])
    return "%d stars in %d games" % (sum(stars.values()), len(stars))


# Which message the My progress sheet shows ("" for none): erased, erasefail, restored, undofail...
SHEET_MSG_JS = r"""() => { const w = document.getElementById("progWarn");
  return w && w.style.display !== "none" ? (w.dataset.msg || "") : ""; }"""


def _open_erase(page):
    """☰, My progress, Start fresh: the confirm sheet is open."""
    if not page.query_selector("#progWrap"):
        page.click("#menuBtn", timeout=3000)
        page.click("#mProg", timeout=3000)
    page.click("#progWipe", timeout=3000)
    page.wait_for_selector("#eraseWrap", timeout=3000)


def start_fresh(page, expect="erased"):
    """Start fresh the way she does it: ☰, My progress, Start fresh, then Erase on the confirm sheet
    once its 600 ms guard has passed. Returns what went wrong, or "" when the sheet ends on the
    message `expect` ("erased", or "erasefail" when no copy can take the erase)."""
    try:
        _open_erase(page)
        page.wait_for_timeout(650)
        page.click("#eraseYes", timeout=3000)
        page.wait_for_selector("#eraseWrap", state="detached", timeout=15000)
        page.wait_for_function("(m) => (%s)() === m" % SHEET_MSG_JS, arg=expect, timeout=15000)
    except PWTimeout as e:
        got = ""
        try:
            got = page.evaluate(SHEET_MSG_JS)
        except Exception:
            pass
        return "Start fresh did not end with %r: the sheet says %r (%s)" % (expect, got, _line(str(e), 100))
    return ""


SHOWN_JS = r"""() => ({ total: totalStars(), coins: state.coins || 0,
                        gender: state.gender || null, mascot: state.mascot || null })"""


def _carried(fx):
    """{gender, mascot}: S-0042's guide and Hindi form, which the fold carries into the guest
    profile and Start fresh keeps. In this fixture only hikmat_current holds them, as on a phone
    signed in with a build that kept them nowhere else, and an erase that removes hikmat_current
    first loses them for good. A fixture where a saved state held them too could not show that, so
    it is refused."""
    want = {f: _dict(_parsed(fx.local.get("hikmat_current"))).get(f) for f in ("gender", "mascot")}
    if not all(want.values()) or any(_dict(st).get(f) for st in fx.seed_states.values() for f in want):
        raise RuntimeError("%s must hold a gender and a mascot in hikmat_current and in no saved state" % fx.name)
    return want


class _Carry:
    """Checks that a state, stored or held in memory, has the guide and Hindi form in `want`
    (_carried), and reports only the first one that does not: every later one follows from it, and
    would crowd the rest of the test's findings out of its line."""

    def __init__(self, want, probs):
        self.want, self.probs, self.lost = want, probs, False

    def check(self, when, where, got):
        have = {f: _dict(got).get(f) or None for f in ("gender", "mascot")}
        if have != self.want and not self.lost:
            self.lost = True
            self.probs.append("%s, %s has gender %s and mascot %s, not %s and %s, which only hikmat_current held"
                              % (when, where, json.dumps(have["gender"]), json.dumps(have["mascot"]),
                                 self.want["gender"], self.want["mascot"]))


def _erased_after_landing(env, fx):
    """(stored guest profile, problems) from a second phone on the build as it is, with no sabotage:
    fx with room, so the fold lands on the first launch, then Start fresh and two more launches.
    An erase made while the fold waited must leave this same profile."""
    ph = env.phone()
    probs = []
    try:
        ph.seed(Fixture(fx.name + ", with room", fx.doc))
        ph.launch("new")
        if ph.page.evaluate("() => localStorage.getItem('hikmat_current') !== null"):
            probs.append("the fold did not land although there was room")
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
        for _ in range(2):
            ph.launch("new")
        raw = ph.page.evaluate(STORAGE_JS)["keys"].get("hikmat_state_guest")
        if ph.errors:
            probs.append(_clip(ph.errors, 3))
    finally:
        ph.close()
    return _parsed(raw), probs


def _attempt_queue(student, size):
    """An attempt queue of at least `size` characters, as recordAttempt() leaves it on a phone that
    was signed in but offline: one record per finished game, each carrying the student's id and
    token. Synthetic, and the token is not one."""
    q = []
    while len(compact(q)) < size:
        n = len(q)
        q.extend({"track": "bazaar", "lesson": "l%02d" % (i % 12 + 1), "activity": "learn", "stars": 3, "score": 5,
                  "total": 5, "coins": 40, "duration_secs": 60, "student": student, "token": "e2e-not-a-token",
                  "client_id": "e2e-%d" % i} for i in range(n, n + 100))
    return compact(q)


def retire_under_full_storage(env, sabotage=None, prelude=None):
    """S3. A phone still signed in as S-0042 when accounts went off, full to the last character, and
    holding nothing that may go to make room: no caches and no queues. retireAccounts() folds her
    account into the guest profile, and on this phone that write cannot land unless something it
    had no right to delete makes room. So hikmat_current has to stay, for the next launch to fold
    again, instead of the account being dropped with its stars left in a key no screen reads, and
    nothing may be removed. Once there is room the fold must land, exactly once."""
    tag, code = "[%s] retire_under_full_storage" % env.mode, "S3"
    fx = load_fixture(SAVES / "retired-account.json", "retired-account on a full phone")
    fx.fill = True
    stars = _starred(fx.seed_states[ACCOUNT_KEY])

    def steps(ph, probs):
        ph.seed(fx)
        seeded = _seeded(ph)["keys"]
        ph.launch("new")
        first = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_current" not in first:
            have = guarded(_parsed(first.get("hikmat_state_guest")))
            short = [p for p, v in stars.items() if have.get(p, 0) < v]
            probs.append("hikmat_current is gone after the first launch, " + (
                "and the guest profile lacks %d of S-0042's %d starred games: the fold was lost" % (len(short), len(stars))
                if short else "although this phone had no room for the fold: something was deleted to make room for it"))
        if first.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed" % ACCOUNT_KEY)
        probs.extend(_states_kept(fx, first) + _left_alone(seeded, first, may_go=CACHE_KEYS + ("hikmat_current",)))
        # Room appears, as it does once she clears some space: the next launch must finish the fold
        # it could not write before. The test takes only the filler, so everything else the first
        # launch left must still be there afterwards.
        ph.page.evaluate(DROP_FILLER_JS)
        left = {k: v for k, v in first.items() if not k.startswith("zz_filler_")}
        ph.launch("new")
        then = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_current" in then:
            probs.append("hikmat_current is still there once there was room, so the fold never landed")
        probs.extend(_fold_landed(fx, then))
        if then.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed after the fold" % ACCOUNT_KEY)
        probs.extend(_left_alone(left, then, may_go=CACHE_KEYS + ("hikmat_current",)))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "on the full phone the fold could not be written and nothing was deleted to make room, so hikmat_current "
             "was kept for the next launch; once there was room the fold landed once, with all of S-0042's %s"
             % _worth(fx)))]


def erase_while_fold_pending(env, sabotage=None, prelude=None):
    """S5. The S3 phone, with S-0042's fold still waiting for room, and she taps Start fresh. That
    erases every star on this phone, the waiting fold's too, just as it would have if the fold had
    landed first. Once there is room, no launch may land the fold and bring S-0042's stars back
    into the profile she has just erased. What the fold carries besides stars stays, as it does
    when the fold lands first: S-0042's guide and Hindi form, which only hikmat_current held, so
    the erase that ends the fold must store them before hikmat_current goes. The profile it leaves
    must be the one a second phone leaves, where the fold landed and she then erased. The account's
    own key stays as it was."""
    tag, code = "[%s] erase_while_fold_pending" % env.mode, "S5"
    fx = load_fixture(SAVES / "retired-account.json", "retired-account on a full phone, then Start fresh")
    fx.fill = True
    want = _carried(fx)

    def steps(ph, probs):
        carry = _Carry(want, probs)
        ph.seed(fx)
        ph.launch("new")
        if ph.page.evaluate("() => localStorage.getItem('hikmat_current') === null"):
            probs.append("hikmat_current was not kept on the full phone, so no fold was waiting and the erase proved nothing")
            return
        if ph.NATIVE is None:
            # The web build has no native store, and this phone has no room for the undo copy, so
            # Start fresh is refused (Task 6): she is told, and nothing on the phone changes.
            before = ph.page.evaluate(STORAGE_JS)["keys"]
            why = start_fresh(ph.page, expect="erasefail")
            if why:
                probs.append("with no room for the undo copy the erase must be refused. " + why)
            probs.extend(_left_alone(before, ph.page.evaluate(STORAGE_JS)["keys"], may_go=CACHE_KEYS))
            return
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        erased = ph.page.evaluate(STORAGE_JS)["keys"]
        guest = _parsed(erased.get("hikmat_state_guest"))
        if guest is None or _starred(guest) or _num(guest.get("coins")):
            probs.append("Start fresh did not reach storage: hikmat_state_guest still holds stars or gems")
        if "hikmat_current" in erased:
            probs.append("hikmat_current is still there after Start fresh")
        if guest is not None:
            carry.check("after Start fresh", "hikmat_state_guest", guest)
        carry.check("after Start fresh", "the state in memory", ph.page.evaluate(SHOWN_JS))
        ph.page.evaluate(DROP_FILLER_JS)
        left = {k: v for k, v in erased.items() if not k.startswith("zz_filler_")}
        for which in ("the first launch with room", "the launch after it"):
            ph.launch("new")
            shown = ph.page.evaluate(SHOWN_JS)
            if shown["total"] or shown["coins"]:
                probs.append("%s shows %s stars and %s gems: the waiting fold landed in the profile she had erased"
                             % (which, shown["total"], shown["coins"]))
                break
            carry.check("on " + which, "the state in memory", shown)
        then = ph.page.evaluate(STORAGE_JS)["keys"]
        if then.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed" % ACCOUNT_KEY)
        probs.extend(_left_alone(left, then, may_go=CACHE_KEYS + ("hikmat_current",)))
        ref, why = _erased_after_landing(env, fx)
        probs.extend("on the phone where the fold landed first, " + w for w in why)
        got = _parsed(then.get("hikmat_state_guest"))
        if ref is None:
            probs.append("the phone where the fold landed first stored no hikmat_state_guest to compare with")
        elif got != ref:
            probs.append("the erased profile differs from the one a fold that landed before the erase leaves: "
                         + _clip(diff(ref, got), 4))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    if env.mode == "web":
        return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
                 "on the web, with no room for the undo copy, Start fresh was refused, she was told, and nothing changed"))]
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "Start fresh while S-0042's fold waited for room ended the fold too: two launches with room show 0 stars "
             "and 0 gems, the erased profile keeps gender %s and mascot %s and is the one a fold that landed first "
             "leaves, and %s is untouched" % (want["gender"], want["mascot"], ACCOUNT_KEY)))]


def erase_refused_while_fold_pending(env, sabotage=None, prelude=None):
    """S7. The S5 phone without a guest profile, as on a phone that was signed in from its first
    launch. Start fresh is then the first write of hikmat_state_guest, and a phone full to the last
    character refuses it, so nothing of the erase is stored. hikmat_current must stay as it was: it
    is the only copy of S-0042's guide and Hindi form, and the fold still to land. Room then
    appears in the same launch, and her next save lands. That save stores the erased profile with
    the guide and Hindi form, and the fold must end with it: left waiting, it would land on the next
    launch inside the profile she erased."""
    tag, code = "[%s] erase_refused_while_fold_pending" % env.mode, "S7"
    fx = load_fixture(SAVES / "retired-account.json", "retired-account without a guest profile on a full phone")
    del fx.local["hikmat_state_guest"], fx.seed_states["hikmat_state_guest"]
    fx.fill = True
    want = _carried(fx)

    def steps(ph, probs):
        carry = _Carry(want, probs)
        ph.seed(fx)
        seeded = _seeded(ph)["keys"]
        ph.launch("new")
        first = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_current" not in first:
            probs.append("hikmat_current was not kept on the full phone, so no fold was waiting and the erase proved nothing")
            return
        if "hikmat_state_guest" in first:
            probs.append("a save landed before Start fresh, so the phone was not full and the refused erase was never tried")
            return
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        erased = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_state_guest" in erased:
            probs.append("localStorage kept the erase, so this phone never refused it and the check proved nothing")
            return
        if erased.get("hikmat_current") != fx.local["hikmat_current"]:
            probs.append("hikmat_current %s although localStorage refused the erase: it was the only copy of gender %s "
                         "and mascot %s, and the fold still to land"
                         % ("went" if "hikmat_current" not in erased else "changed", want["gender"], want["mascot"]))
        if erased.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed by the refused erase" % ACCOUNT_KEY)
        probs.extend(_left_alone(seeded, erased, may_go=CACHE_KEYS + ("hikmat_current",)))
        # Room appears in the same launch, and her next save, the one her next game makes, lands.
        ph.page.evaluate(DROP_FILLER_JS)
        r = ph.page.evaluate(SAVE_JS)
        if not r["ok"]:
            probs.append("her next save threw %s: %s" % (r["name"], _line(r["text"], 120)))
        elif r["value"] is not True:
            probs.append("her next save, with room, returned %s, not true" % json.dumps(r["value"]))
        stored = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_current" in stored:
            probs.append("hikmat_current is still there after her next save stored the erased profile, so the fold "
                         "still waits to land in it")
        guest = _parsed(stored.get("hikmat_state_guest"))
        if guest is None:
            probs.append("her next save stored no hikmat_state_guest")
        else:
            if _starred(guest) or _num(guest.get("coins")):
                probs.append("the profile her next save stored holds stars or gems, so it is not the erased one")
            carry.check("after her next save", "hikmat_state_guest", guest)
        left = {k: v for k, v in stored.items() if not k.startswith("zz_filler_")}
        for which in ("the next launch", "the launch after it"):
            ph.launch("new")
            shown = ph.page.evaluate(SHOWN_JS)
            if shown["total"] or shown["coins"]:
                probs.append("%s shows %s stars and %s gems: the waiting fold landed in the profile she had erased"
                             % (which, shown["total"], shown["coins"]))
                break
            carry.check("on " + which, "the state in memory", shown)
        then = ph.page.evaluate(STORAGE_JS)["keys"]
        if then.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed" % ACCOUNT_KEY)
        probs.extend(_left_alone(left, then, may_go=CACHE_KEYS + ("hikmat_current",)))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "the full phone refused the erase and kept hikmat_current as it was; once her next save landed, it "
             "stored the erased profile with gender %s and mascot %s, the fold ended with it, and two launches "
             "show 0 stars and 0 gems" % (want["gender"], want["mascot"])))]


def retire_frees_queue_room(env, sabotage=None, prelude=None):
    """S6. A phone that stayed signed in as S-0042 through a long time offline, full of its own
    unsent attempt queue, and with no caches. retireAccounts() deletes that queue whatever happens,
    because it can never be sent, and it has to do so before it writes the fold. Then the fold lands
    in the room the queue held, on the first launch, and she never sees her progress without
    S-0042's stars. Nothing but the queue and hikmat_current may go."""
    tag, code = "[%s] retire_frees_queue_room" % env.mode, "S6"
    fx = load_fixture(SAVES / "retired-account.json", "retired-account, full of its own unsent queue")
    fx.local = dict(fx.local, hikmat_attempt_queue=_attempt_queue("S-0042", 3 * len(fx.local[ACCOUNT_KEY])))
    fx.fill = True

    def steps(ph, probs):
        ph.seed(fx)
        seeded = _seeded(ph)
        if not seeded["full"]:
            probs.append("the phone was not full before the launch, so the launch proved nothing")
        ph.launch("new")
        first = ph.page.evaluate(STORAGE_JS)["keys"]
        if "hikmat_current" in first:
            probs.append("hikmat_current is still there after the first launch: the fold was written before the queue's "
                         "room was freed, so she plays this launch without S-0042's stars")
        else:
            probs.extend(_fold_landed(fx, first))
        if "hikmat_attempt_queue" in first:
            probs.append("the attempt queue, which can never be sent, is still there")
        if first.get(ACCOUNT_KEY) != fx.local[ACCOUNT_KEY]:
            probs.append("%s was changed or removed" % ACCOUNT_KEY)
        probs.extend(_states_kept(fx, first) + _left_alone(seeded["keys"], first,
                                                           may_go=CACHE_KEYS + ("hikmat_current", "hikmat_attempt_queue")))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "the fold landed on the first launch, in the room the unsent queue held, with all of S-0042's %s, and "
             "nothing but the queue and hikmat_current was removed" % _worth(fx)))]


LIVE_TAGLINE = "e2e: the tagline the server sent"
LIVE_JS = r"""() => ({ live: backendLive === true, tagline: SETTINGS.tagline.en,
                       fresh: COURSES.length > 0 && COURSES.every(c => c.e2eLive === true) })"""


def storage_full_live_content(env, sabotage=None, prelude=None, idb=True):
    """S4. The same full phone, with the caches in it, now online. The server sends lessons that
    take a little more room than the cached copy. Caching them must not throw: a throw there
    skipped the settings and grade structure the server had just sent, and marked a server that
    had answered as unreachable, which is what lesson videos wait for. Making room may drop the
    content caches, and nothing else.

    S4 is the phone as it is since Task 3: the launch moves the caches into IndexedDB, and the
    server's lessons must land there. S4b is the same phone with IndexedDB refusing to open, where
    the lessons go back to localStorage through writeLocal() and the full phone is still full."""
    tag, code = "[%s] storage_full_live_content%s" % (env.mode, "" if idb else " (IndexedDB blocked)"), "S4" if idb else "S4b"
    if not idb:
        prelude = IDB_BLOCKED + (prelude or "")
    fx = load_fixture(SAVES / "full-storage.json", "full-storage, online")
    fx.local = dict(fx.local, hikmat_settings_cache=SETTINGS_CACHE)
    live = [dict(c, e2eLive=True) for c in CURRICULUM]

    def steps(ph, probs):
        ph.seed(fx)
        before = _seeded(ph)
        if not before["full"]:
            probs.append("the phone was not full before the launch, so the launch proved nothing")
        ph.answer(r"^/api/method/hikmat\.api\.get_courses$", status=200, content_type="application/json",
                  body=compact({"message": live}))
        ph.answer(r"^/api/method/hikmat\.api\.get_settings$", status=200, content_type="application/json",
                  body=compact({"message": {"appName": "Bodhya Learn", "taglineEn": LIVE_TAGLINE}}))
        ph.launch("new")
        r = ph.page.evaluate(LIVE_JS)
        if not r["fresh"]:
            probs.append("the game is not showing the lessons the server sent")
        if not r["live"]:
            probs.append("backendLive is false although the server answered")
        if r["tagline"] != LIVE_TAGLINE:
            probs.append("the settings the server sent were never applied")
        after = ph.page.evaluate(STORAGE_JS)["keys"]
        # The settings cache may change, since the server has just sent new settings, but it must
        # still be there: it is not one of the caches a write may drop to make room.
        probs.extend(_states_kept(fx, after) + _left_alone(before["keys"], after, may_go=CACHE_KEYS,
                                                           may_change=("hikmat_settings_cache",)))
        if idb:
            got = _content_db(ph, lambda db: all(c.get("e2eLive") for c in _list(db["kv"].get("hikmat_courses_cache"))))
            if not got.get("kv", {}).get("hikmat_courses_cache"):
                probs.append("the server's lessons are not in IndexedDB")
            elif not all(c.get("e2eLive") for c in _list(got["kv"]["hikmat_courses_cache"])):
                probs.append("IndexedDB still holds the old lessons, not the ones the server sent")

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "the server's lessons and settings were taken on the full phone, and caching them removed nothing but "
             "the content caches" + (", and the lessons are cached in IndexedDB" if idb else "")))]


# ---------------------------------------------------------------- Task 3: content caches -------

# IndexedDB refuses to open, as in a WebView where it is switched off or broken. The content store
# must fall back to localStorage, where the caches were before Task 3, and lose nothing.
IDB_BLOCKED = ("/* e2e: IndexedDB refuses to open */(function(){IDBFactory.prototype.open=function(){"
               "throw new DOMException('IndexedDB is blocked in this test','SecurityError');};})();")
# IndexedDB opens, but refuses every write, as a phone whose disk is full does. A cache may leave
# localStorage only once IndexedDB holds it, so here every cache must stay where it was.
IDB_PUT_REFUSED = ("/* e2e: IndexedDB refuses every write */(function(){IDBObjectStore.prototype.put=function(){"
                   "throw new DOMException('The quota has been exceeded.','QuotaExceededError');};})();")

# What the content database holds, read without creating it: a build that never made it must not
# have it made by the test. `store` is false when the database exists without its kv store.
CONTENT_DB_JS = r"""async () => {
  const names = (await indexedDB.databases()).map(d => d.name);
  if (!names.includes("hikmat_content")) return { exists: false, store: false, kv: {} };
  return await new Promise(res => {
    const rq = indexedDB.open("hikmat_content");
    rq.onerror = () => res({ exists: true, store: false, kv: {}, error: String(rq.error) });
    rq.onsuccess = () => {
      const db = rq.result;
      if (!db.objectStoreNames.contains("kv")) { db.close(); return res({ exists: true, store: false, kv: {} }); }
      const kv = {};
      const cur = db.transaction("kv", "readonly").objectStore("kv").openCursor();
      cur.onsuccess = () => { const c = cur.result; if (c) { kv[c.key] = c.value; c.continue(); }
                              else { db.close(); res({ exists: true, store: true, kv }); } };
      cur.onerror = () => { db.close(); res({ exists: true, store: true, kv, error: String(cur.error) }); };
    };
  });
}"""

# Where the content on screen came from. Every cache _marked_caches() seeds carries e2eCached, and
# no file or server ever sends it, so content that has it can only have come from a cache.
FROM_CACHE_JS = r"""() => ({
  courses: COURSES.length,
  coursesMarked: COURSES.length > 0 && COURSES.every(c => c && c.e2eCached === true),
  bank: LEVEL_BANK.bank.length,
  bankMarked: LEVEL_BANK.bank.length > 0 && LEVEL_BANK.bank.every(q => q && q.e2eCached === true),
  bandsMarked: Array.isArray(BANDS) && BANDS.length > 0 && BANDS.every(b => b && b.e2eCached === true),
})"""


def _list(x):
    return x if isinstance(x, list) else []


def _raw(ph, *keys):
    """The stored strings themselves. STORAGE_JS gives other keys as lengths, and a JS length counts
    UTF-16 units where Python's counts characters, so the two never agree on text with emoji in it."""
    return ph.page.evaluate("(ks) => ks.map(k => localStorage.getItem(k))", list(keys))


def _content_db(ph, done=None, wait_s=3.0):
    """CONTENT_DB_JS, read again for up to wait_s seconds until done(db) holds. Nothing awaits a
    cache write, so one may still be on its way when the boot is over."""
    end = time.monotonic() + wait_s
    while True:
        db = ph.page.evaluate(CONTENT_DB_JS)
        if done is None or (db.get("store") and done(db)) or time.monotonic() >= end:
            return db
        ph.page.wait_for_timeout(100)


@functools.lru_cache(maxsize=1)
def _marked_caches():
    """The three caches at real size, parsed, each item marked e2eCached (see FROM_CACHE_JS)."""
    v = {k: json.loads(raw) for k, raw in cache_values().items()}
    v["hikmat_courses_cache"] = [dict(c, e2eCached=True) for c in v["hikmat_courses_cache"]]
    v["hikmat_structure_cache"] = dict(v["hikmat_structure_cache"],
                                       bands=[dict(b, e2eCached=True) for b in v["hikmat_structure_cache"]["bands"]])
    v["hikmat_testbank_cache"] = dict(v["hikmat_testbank_cache"],
                                      bank=[dict(q, e2eCached=True) for q in v["hikmat_testbank_cache"]["bank"]])
    return v


def _cached_phone():
    """v7-midway on a phone that has been online: the three marked caches and the settings cache."""
    mid = load_fixture(SAVES / "v7-midway.json", "v7-midway")
    local = dict(mid.local, hikmat_settings_cache=SETTINGS_CACHE)
    local.update({k: compact(v) for k, v in _marked_caches().items()})
    return Fixture("v7-midway with its caches", {"kind": "storage-snapshot", "note": "v7-midway, online before",
                                                 "localStorage": local})


def _go_offline(ph):
    """No server and no bundled data files: whatever content the next launch shows, it read from a cache."""
    ph.answer(r"^/api/method/hikmat\.api\.", abort=True)
    ph.answer(r"^/assets/hikmat/(curriculum|testbank)\.json$", status=404)


def _from_cache(ph, where):
    """What is wrong with the content on screen, for content that must have come from a cache."""
    seen = ph.page.evaluate(FROM_CACHE_JS)
    out = []
    if seen["courses"] != CURRICULUM_LEN or not seen["coursesMarked"]:
        out.append("offline, the lessons are not the cached ones (%d tracks%s) although %s held them"
                   % (seen["courses"], ", unmarked" if not seen["coursesMarked"] else "", where))
    if seen["bank"] != TESTBANK_LEN or not seen["bankMarked"]:
        out.append("offline, the test bank is not the cached one (%d questions%s) although %s held it"
                   % (seen["bank"], ", unmarked" if not seen["bankMarked"] else "", where))
    if not seen["bandsMarked"]:
        out.append("offline, the grade bands are not the cached ones although %s held them" % where)
    return out


def caches_move_to_idb(env, sabotage=None, prelude=None):
    """C1. A phone that has been online holds the three content caches in localStorage, beside her
    progress, where a full phone made them cost her stars. The first launch moves each one into
    IndexedDB, unchanged, and only then takes it out of localStorage. The settings cache is small
    and stays. C4 then opens v7 on what the move left: v7 knows only the localStorage caches, so it
    must fall back to the bundled curriculum and still show her the same stars, level and lessons."""
    tag = "[%s] caches_move_to_idb" % env.mode
    fx = _cached_phone()
    caches = _marked_caches()
    rollback = []

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        if "contentReady" not in ph.boot_result and "contentReadyError" not in ph.boot_result:
            probs.append("the build has no contentReady, so nothing moves the caches")
        after = ph.page.evaluate(STORAGE_JS)["keys"]
        left = [k for k in CACHE_KEYS if k in after]
        if left:
            probs.append("%s still in localStorage" % _keys(left))
        if _raw(ph, "hikmat_settings_cache") != [SETTINGS_CACHE]:
            probs.append("hikmat_settings_cache was moved or changed, and it stays in localStorage")
        db = _content_db(ph, lambda d: all(k in d["kv"] for k in CACHE_KEYS))
        if not db.get("store"):
            probs.append("IndexedDB has no hikmat_content database with a kv store%s"
                         % ((": " + _line(db["error"], 80)) if db.get("error") else ""))
        else:
            for k in CACHE_KEYS:
                if k not in db["kv"]:
                    probs.append("%s is not in IndexedDB" % k)
                elif db["kv"][k] != caches[k]:
                    probs.append("%s changed on its way into IndexedDB: %s" % (k, _clip(diff(caches[k], db["kv"][k], values=False), 2)))
        probs.extend(_states_kept(fx, after))
        new = ph.snap()
        ph.launch("old")
        old = ph.snap()
        if old["coursesLen"] != CURRICULUM_LEN:
            rollback.append("v7 shows %d tracks, not the %d of curriculum.json" % (old["coursesLen"], CURRICULUM_LEN))
        if old["total"] != new["total"] or old["level"] != new["level"]:
            rollback.append("v7 shows %s stars at level %s, the new build %s at level %s"
                            % (old["total"], old["level"], new["total"], new["level"]))
        shut = [x for x in new["playedOpen"] if x not in old["playedOpen"]]
        if shut:
            rollback.append("v7 shuts %d lessons she played: %s" % (len(shut), _clip(["/".join(x) for x in shut], 3)))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    out = [("C1", not probs, "%s C1 %s" % (tag, _clip(probs, 4) if probs else
            "the three caches moved into IndexedDB unchanged and left localStorage, the settings cache stayed, "
            "and every saved state is intact"))]
    if not probs or rollback:
        out.append(("C4", not rollback, "%s C4 %s" % (tag, _clip(rollback, 3) if rollback else
                    "v7, opened on what the move left, shows every track of curriculum.json and the same stars, "
                    "level and played lessons")))
    return out


def offline_uses_idb_cache(env, sabotage=None, prelude=None):
    """C2. After the move, a launch with no server and no bundled data files. Everything she sees
    must come from IndexedDB: the lessons, the grade bands and the test bank."""
    tag, code = "[%s] offline_uses_idb_cache" % env.mode, "C2"
    fx = _cached_phone()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        left = [k for k in CACHE_KEYS if k in ph.page.evaluate(STORAGE_JS)["keys"]]
        if left:
            probs.append("%s still in localStorage after the first launch, so this launch could read them there" % _keys(left))
        _go_offline(ph)
        ph.launch("new")
        probs.extend(_from_cache(ph, "IndexedDB"))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
             "with no server and no data files, the lessons, grade bands and test bank all came from IndexedDB"))]


def idb_blocked_falls_back(env, sabotage=None, prelude=None):
    """C3. The C1 phone where IndexedDB refuses to open (C3), and where it opens but refuses every
    write (C3b). Content still loads with no page error, every cache stays in localStorage exactly
    as it was, since there is nowhere it could go, and offline the game still reads them there."""
    out = []
    for code, block, what in (("C3", IDB_BLOCKED, "IndexedDB blocked"), ("C3b", IDB_PUT_REFUSED, "IndexedDB refusing writes")):
        tag = "[%s] idb_blocked_falls_back (%s)" % (env.mode, what)
        fx = _cached_phone()

        def steps(ph, probs):
            ph.seed(fx)
            ph.launch("new")
            after = ph.page.evaluate(STORAGE_JS)["keys"]
            gone = [k for k, v in zip(CACHE_KEYS, _raw(ph, *CACHE_KEYS)) if v != fx.local[k]]
            if gone:
                probs.append("%s left localStorage or changed, with nowhere to go" % _keys(gone))
            probs.extend(_states_kept(fx, after))
            _go_offline(ph)
            ph.launch("new")
            probs.extend(_from_cache(ph, "localStorage"))

        probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=block + (prelude or "")), steps)
        out.append((code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else
                    "content loaded with no page error, every cache stayed in localStorage as it was, and offline "
                    "the game read them there")))
    return out


# ---------------------------------------------------------------- level test, no account ------
# The two checks of e2e-levels.py that still hold with accounts off: passing the level test
# promotes her, and nothing ever locks. e2e-levels.py signs a learner up on a running site; it
# stays for the web build, which still signs learners in, until step 5. Step 3 replaces the whole
# ladder with checkpoints, and its tests replace these.

# Every published lesson that lessonLock() shuts, as "course/lesson".
LOCKS_JS = r"""() => { const out = [];
  COURSES.forEach(c => { if (!c.published) return; (c.lessons || []).forEach(ls => { if (lessonLock(c, ls)) out.push(c.key + "/" + ls.key); }); });
  return out; }"""
# 110 stars on level-1 lessons, as e2e-levels.py earns them: the whole of eng-foundation first,
# then lessons with quiz items, so the paper has enough questions.
EARN_L1_JS = r"""() => {
  let stars = 0;
  const order = [COURSES.find(c => c.key === 'eng-foundation')].concat(COURSES.filter(c => c.key !== 'eng-foundation'));
  order.forEach(c => { if (!c || !c.published) return; c.lessons.forEach(ls => {
    if (lessonLevel(c, ls) !== 1 || stars >= 110) return;
    if (c.key !== 'eng-foundation' && !(ls.quiz && ls.quiz.length)) return;
    levelsFor(ls).forEach(lv => { setStars(c.key, ls.key, lv.id, 3); stars += 3; });
  }); });
  save(); refreshTop();
  return [stars, levelTestDue(1)];
}"""


def _sit_level_test(page):
    """Sit the level-1 test through the game's own screens, from the trophy on the trail: every
    question answered right except the third, which is left to time out. That is 19 of 20, 95%.
    Returns what went wrong, or ""."""
    try:
        page.evaluate("renderTrack(COURSES.find(c => c.key === 'eng-foundation'))")
        page.locator("#path .node", has_text="🏆").first.click(timeout=3000)
        page.click("#tstart", timeout=3000)
        page.wait_for_selector("#ttimer", timeout=5000)
        n = page.evaluate("TEST.paper.qs.length")
        for i in range(n):
            page.wait_for_function("(i) => TEST && TEST.idx === i && !TEST._answered", arg=i, timeout=5000)
            if i == 2:
                page.evaluate("TEST.qDeadline = Date.now() + 800")
                page.wait_for_function("(i) => TEST && (TEST.idx !== i || TEST._answered)", arg=i, timeout=6000)
                continue
            ans = page.evaluate("TEST.paper.qs[TEST.idx].answer")
            page.locator(".quizopt", has_text=re.compile("^" + re.escape(ans) + "$")).first.click(timeout=3000)
        page.wait_for_selector(".result", timeout=8000)
        page.click("#tdone", timeout=3000)
        page.wait_for_selector("#path .step", timeout=5000)
    except PWTimeout as e:
        return "the level test stopped answering her (%s)" % _line(str(e), 120)
    return ""


def level_test_promotes(env, sabotage=None, prelude=None):
    """LT1. A learner with no account, on a phone with nothing played, earns 110 level-1 stars,
    and the level-1 test comes due. She passes it with 95%: she is at level 2, the pass is in her
    stored state, and both are still there after a relaunch. Nothing is sent: with accounts off
    there is nobody to send it for. LT2. Nothing locks: not at zero stars, not while the test is
    due, not after she passes, and not after the relaunch."""
    tag = "[%s] level_test_promotes" % env.mode
    erased = load_fixture(SAVES / "post-erase.json", "post-erase")
    fx = Fixture("a learner with no account and nothing played", {
        "kind": "bodhya-progress", "v": 1, "app": APP_ID, "savedAt": SAVED_AT,
        "state": dict(erased.seed_states["hikmat_state_guest"], lang="en")})
    promo, locks, sent = [], [], []

    def shut(when, page):
        got = page.evaluate(LOCKS_JS)
        if got:
            locks.append("%s, %d lessons are locked: %s" % (when, len(got), _clip(got, 3)))

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        ph.page.on("request", lambda r: sent.append(r.url) if "submit_test" in r.url else None)
        shut("at zero stars", ph.page)
        stars, due = ph.page.evaluate(EARN_L1_JS)
        if not due:
            promo.append("the level-1 test is not due after %d level-1 stars" % stars)
        shut("with the level-1 test due", ph.page)
        why = _sit_level_test(ph.page)
        if why:
            promo.append(why)
        lvl = ph.page.evaluate("studentLevel()")
        if lvl != 2:
            promo.append("after passing she is at level %s, not 2" % lvl)
        shut("after passing", ph.page)
        ph.launch("new")
        lvl = ph.page.evaluate("studentLevel()")
        stored = _dict(_dict(_parsed(ph.page.evaluate(STORAGE_JS)["keys"].get("hikmat_state_guest"))).get("levelTests")).get("1")
        if lvl != 2:
            promo.append("after a relaunch she is at level %s, not 2" % lvl)
        if not _dict(stored).get("passed"):
            promo.append("after a relaunch the stored state does not hold the level-1 pass")
        shut("after a relaunch", ph.page)
        if sent:
            promo.append("the result was sent to %d submit_test request(s), with accounts off" % len(sent))

    probs = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    lt1, lt2 = probs + promo, probs + locks
    return [("LT1", not lt1, "%s LT1 %s" % (tag, _clip(lt1, 4) if lt1 else
             "she passed the level-1 test with 95% and is at level 2, stored and still there after a relaunch, "
             "and nothing was sent")),
            ("LT2", not lt2, "%s LT2 %s" % (tag, _clip(lt2, 4) if lt2 else
             "no lesson locked at zero stars, with the test due, after passing, or after a relaunch"))]


# ---------------------------------------------------------------- Task 4: the native copy -----
# Rule 6: every save is mirrored to Capacitor Preferences, outside the WebView, but only once the
# launch has read the native copy, and the launch merges that copy back in (rule 2: the higher
# value wins; an erase, marked by a higher eraseGen, wins outright).

GUEST = "hikmat_state_guest"
EGEN_JS = "() => (typeof state.eraseGen === 'number' ? state.eraseGen : 0)"
ROOT_HINDI_JS = r"""() => /[ऀ-ॿ]/.test((document.getElementById("root") || document.body).innerText)"""
# Records, before any game script, the first localStorage write and whether BOOT_STATE existed by
# then. A const read before its declaration throws, hence the try.
FIRST_SET = ("/* e2e: the first localStorage write */(function(){window.__e2eFirstSet=null;var s=Storage.prototype.setItem;"
             "Storage.prototype.setItem=function(k){if(window.__e2eFirstSet===null){var b=false;"
             "try{b=typeof BOOT_STATE!=='undefined';}catch(e){}window.__e2eFirstSet={key:String(k),bootState:b};}"
             "return s.apply(this,arguments);};})();")


def _mid():
    return load_fixture(SAVES / "v7-midway.json", "v7-midway")


def _local(ph, key=GUEST):
    return ph.page.evaluate("(k) => localStorage.getItem(k)", key)


def _settled(ph, wait_s=3.0, key=GUEST):
    """(local, native) for `key`, once they agree or wait_s has passed. A mirror write is not awaited
    by anything, so it can still be on its way when a launch is over."""
    end = time.monotonic() + wait_s
    while True:
        loc, nat = _local(ph, key), ph.NATIVE.prefs.get(key)
        if (loc is not None and loc == nat) or time.monotonic() >= end:
            return loc, nat
        ph.page.wait_for_timeout(100)


def _agree(ph, probs, when):
    loc, nat = _settled(ph)
    if nat is None:
        probs.append("%s the native copy is missing" % when)
    elif loc != nat:
        probs.append("%s the native copy differs from localStorage: %s" % (when, _clip(diff(_parsed(loc), _parsed(nat), values=False), 3)))
    return _parsed(loc), _parsed(nat)


def _settings(st):
    s = _dict(st)
    return {f: s.get(f) for f in ("lang", "theme", "sound", "gender", "mascot")}


def _lowered_stars(st, by=1, coins=500):
    """st with every star `by` lower (not below 0) and `coins` fewer gems: an older copy of her."""
    out = json.loads(compact(st))
    for ck, ls in _dict(out.get("progress")).items():
        for lk, g in _dict(ls).items():
            for aid, v in _dict(g).items():
                g[aid] = max(0, (_num(v) or 0) - by)
    out["coins"] = max(0, (_num(out.get("coins")) or 0) - coins)
    return out


def _rel(code, tag, probs, good):
    return [(code, not probs, "%s %s %s" % (tag, code, _clip(probs, 4) if probs else good))]


def mirror_follows_save(env, sabotage=None, prelude=None):
    """N1. Within 2 s of a save, the native copy holds what localStorage holds."""
    tag = "[%s] mirror_follows_save" % env.mode
    fx = _mid()
    (ck, lk), = _fresh_lessons(fx, 1)

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        _, nat = _agree(ph, probs, "2 s after her save,")
        if nat is not None and _learn_star(nat, ck, lk) != 3:
            probs.append("the native copy does not hold the new star at %s/%s" % (ck, lk))

    return _rel("N1", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "the native copy held her new star, and all of localStorage's copy, within 2 s of the save")


def first_launch_seeds_mirror(env, sabotage=None, prelude=None):
    """N2. The first launch of v8 on a v7 phone: nothing native yet. The launch writes it."""
    tag = "[%s] first_launch_seeds_mirror" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        _, nat = _agree(ph, probs, "after the first launch,")
        if nat is not None:
            probs.extend(lowered(fx.seed_states[GUEST], nat, "native " + GUEST))

    return _rel("N2", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "the first launch seeded the native copy with all of her progress")


def empty_webview_restored(env, sabotage=None, prelude=None):
    """N3. Her WebView's data is gone and the native copy holds everything. The launch must bring
    it all back, her settings with it, write it to localStorage, and draw the screen in them."""
    tag = "[%s] empty_webview_restored" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed_local({})
        ph.NATIVE.prefs[GUEST] = fx.local[GUEST]
        ph.launch("new")
        if ph.boot_result.get("nativeRestore") is not True:
            probs.append("nativeRestore resolved %s, not true, although it brought her progress back"
                         % json.dumps(ph.boot_result.get("nativeRestore")))
        loc, _ = _agree(ph, probs, "after the restore,")
        if loc is None:
            probs.append("localStorage was not rewritten")
        else:
            probs.extend(lowered(seed, loc, GUEST) + fields_lost(seed, loc, GUEST))
        mem = ph.page.evaluate("() => JSON.parse(JSON.stringify(state))")
        if _settings(mem) != _settings(seed):
            probs.append("settings are %s, not hers (%s)" % (json.dumps(_settings(mem)), json.dumps(_settings(seed))))
        if seed.get("lang") == "hi" and not ph.page.evaluate(ROOT_HINDI_JS):
            probs.append("the screen was not drawn again in her language")

    return _rel("N3", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "with an empty WebView the launch brought back every star and setting from the native copy, rewrote "
                "localStorage, and drew the screen in her language")


def native_lower_never_wins(env, sabotage=None, prelude=None):
    """N4. The native copy is older: fewer stars, fewer gems. Nothing of hers is lowered, and the
    native copy is raised to match."""
    tag = "[%s] native_lower_never_wins" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed(fx)
        ph.NATIVE.prefs[GUEST] = compact(_lowered_stars(seed))
        ph.launch("new")
        loc, nat = _agree(ph, probs, "after the launch,")
        # Gems first: there is one gem count and hundreds of stars, so the report, which shows
        # only the first few, still says when her gems went down too.
        gems_first = lambda xs: sorted(xs, key=lambda x: " coins " not in x)
        if loc is not None:
            probs.extend(gems_first(lowered(seed, loc, GUEST)))
        if nat is not None:
            probs.extend(gems_first(lowered(seed, nat, "native " + GUEST)))

    return _rel("N4", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "an older native copy lowered nothing, and was raised to her stars and gems")


def relaunch_does_not_inflate(env, sabotage=None, prelude=None):
    """N5. Both copies the same. Three launches leave attempts, gems and seen-lists as they were:
    the launch merge is one learner meeting herself, not two learners adding up."""
    tag = "[%s] relaunch_does_not_inflate" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed(fx)
        ph.NATIVE.prefs[GUEST] = fx.local[GUEST]
        for n in range(3):
            ph.launch("new")
            loc, _ = _agree(ph, probs, "after launch %d," % (n + 1))
            ch = changed_numbers(seed, loc or {})
            if ch:
                probs.append("after launch %d: %s" % (n + 1, _clip(["%s %s -> %s" % (p, a, b) for p, (a, b) in sorted(ch.items())], 3)))
                break
            seen = {k: len(v) for k, v in _dict(_dict(loc).get("testSeen")).items()}
            want = {k: len(v) for k, v in _dict(seed.get("testSeen")).items()}
            if seen != want:
                probs.append("after launch %d the seen-question lists are %s long, not %s" % (n + 1, seen, want))
                break

    return _rel("N5", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "three launches with both copies alike left attempts, gems and seen-lists as they were")


def erase_stays_erased(env, sabotage=None, prelude=None):
    """N6. Start fresh, and the native copy loses that write. The erase still holds on the next
    launch and the one after: the stale native copy has the lower eraseGen, so it cannot bring
    the erased stars back."""
    tag = "[%s] erase_stays_erased" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        _agree(ph, probs, "before Start fresh,")
        ph.NATIVE.drop_next_write = GUEST      # the erase's own write, not the undo copy before it
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        for which in ("the next launch", "the launch after it"):
            ph.launch("new")
            shown, egen = ph.page.evaluate(SHOWN_JS), ph.page.evaluate(EGEN_JS)
            if shown["total"] or shown["coins"]:
                probs.append("%s shows %s stars and %s gems: the stale native copy brought them back"
                             % (which, shown["total"], shown["coins"]))
                break
            if egen != 1:
                probs.append("%s has eraseGen %s, not 1" % (which, egen))

    return _rel("N6", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "an erase whose native write was lost stayed erased for two launches, with eraseGen 1")


def _s7_phone():
    fx = load_fixture(SAVES / "retired-account.json", "retired-account without a guest profile on a full phone")
    del fx.local[GUEST], fx.seed_states[GUEST]
    fx.fill = True
    return fx


def erase_with_waiting_fold_and_stale_mirror(env, sabotage=None, prelude=None):
    """N6b. The merges that can follow an erase, all at once. The 232-star bug was a fold that
    landed after Start fresh, and the launch merge is the same kind of merge.
    (a) The S5 phone: full, S-0042's fold waiting. The native copy is stale, with the guest
        profile's stars and eraseGen 0, and it loses the erase's write. Room then appears.
    (b) The S7 phone: full and with no guest profile, so localStorage refuses the erase, and the
        native copy, stale again, takes it. Room then appears, and the first launch with room
        lands the fold before it merges: the native copy's higher eraseGen must still win.
    In both, two launches show 0 stars and 0 gems with S-0042's guide and Hindi form, and eraseGen
    is at least 1 in localStorage and in the native copy."""
    tag = "[%s] erase_with_waiting_fold_and_stale_mirror" % env.mode
    probs = []
    for case, fx, drop in (("(a)", load_fixture(SAVES / "retired-account.json", "retired-account on a full phone"), GUEST),
                           ("(b)", _s7_phone(), False)):
        fx.fill = True
        want = _carried(fx)
        stale = dict(_dict(fx.seed_states.get(GUEST)) or _lowered_stars(fx.seed_states[ACCOUNT_KEY], by=0, coins=0),
                     eraseGen=0)

        def steps(ph, pr):
            carry = _Carry(want, pr)
            ph.seed(fx)
            ph.NATIVE.prefs[GUEST] = compact(stale)
            ph.launch("new")
            if ph.page.evaluate("() => localStorage.getItem('hikmat_current') === null"):
                pr.append("no fold was waiting, so the erase proved nothing")
                return
            ph.NATIVE.prefs[GUEST] = compact(stale)      # stale again, whatever the launch wrote
            ph.NATIVE.drop_next_write = drop
            why = start_fresh(ph.page)
            if why:
                pr.append(why)
                return
            ph.page.evaluate(DROP_FILLER_JS)
            for which in ("the first launch with room", "the launch after it"):
                ph.launch("new")
                shown = ph.page.evaluate(SHOWN_JS)
                if shown["total"] or shown["coins"]:
                    pr.append("%s shows %s stars and %s gems" % (which, shown["total"], shown["coins"]))
                    break
                carry.check("on " + which, "the state in memory", shown)
            loc, nat = _settled(ph)
            for where, st in (("localStorage", _parsed(loc)), ("the native copy", _parsed(nat))):
                if (_num(_dict(st).get("eraseGen")) or 0) < 1:
                    pr.append("%s has eraseGen %s, not at least 1" % (where, json.dumps(_dict(st).get("eraseGen"))))

        got = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
        probs.extend("%s %s" % (case, p) for p in got)
    return _rel("N6b", tag, probs,
                "(a) a stale native copy that lost the erase's write, and (b) a fold that landed after an erase only "
                "the native copy took, both left 0 stars and 0 gems for two launches, with the guide and Hindi form "
                "kept and eraseGen at least 1 in both copies")


def erase_refused_everywhere(env, sabotage=None, prelude=None):
    """N6c. The S7 phone, where localStorage refuses the erase, and no native copy takes it either:
    the web build has none, and in the app the native side refuses every write (its disk is full
    too). A single lost write is not enough: the erase writes again and reads back. She
    must be told it failed, never "Progress erased", and nothing changes: once there is room, the
    fold lands with S-0042's stars, as it would have without the tap."""
    tag = "[%s] erase_refused_everywhere" % env.mode
    fx = _s7_phone()
    worth = len(_starred(fx.seed_states[ACCOUNT_KEY]))

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        if ph.NATIVE is not None:
            _settled(ph)
            ph.NATIVE.refuse_writes = True
        why = start_fresh(ph.page, expect="erasefail")
        if why:
            probs.append("no copy could take the erase, so she must be told it failed. " + why)
            return
        if ph.page.evaluate("() => localStorage.getItem('hikmat_current') === null"):
            probs.append("hikmat_current went although nothing of the erase was stored")
        if ph.NATIVE is not None:
            ph.NATIVE.refuse_writes = False
        ph.page.evaluate(DROP_FILLER_JS)
        ph.page.evaluate(SAVE_JS)
        ph.launch("new")
        have = _starred(_parsed(_local(ph)))
        if len(have) < worth:
            probs.append("once there was room the guest profile holds %d of S-0042's %d starred games: the erase "
                         "she was told had failed took them" % (len(have), worth))

    return _rel("N6c", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "no copy took the erase, so she was told it failed, hikmat_current stayed, and once there was room the "
                "fold landed with all of S-0042's starred games")


def full_storage_native_carries(env, sabotage=None, prelude=None):
    """N7. S2's phone, full with nothing to drop: localStorage refuses her save, and the native copy
    takes it. After a relaunch the star is in her state."""
    tag = "[%s] full_storage_native_carries" % env.mode
    fx = load_fixture(SAVES / "full-storage.json", "full-storage without caches")
    fx.with_caches = False
    (ck, lk), = _fresh_lessons(fx, 1)

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        r = ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        if r.get("value") is not False:
            probs.append("save() returned %s, so localStorage was not full and this proved nothing" % json.dumps(r.get("value")))
        end = time.monotonic() + 3
        while _learn_star(_parsed(ph.NATIVE.prefs.get(GUEST)), ck, lk) != 3 and time.monotonic() < end:
            ph.page.wait_for_timeout(100)
        ph.launch("new")
        if ph.page.evaluate("([ck, lk]) => getStars(ck, lk, 'learn')", [ck, lk]) != 3:
            probs.append("after a relaunch the star at %s/%s is not in her state" % (ck, lk))

    return _rel("N7", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "a save the full localStorage refused reached the native copy, and the relaunch brought the star back")


def boot_save_cannot_clobber_native(env, sabotage=None, prelude=None):
    """N8. The WebView still holds S-0042's account and no guest profile, and the native copy holds
    v7-midway, read slowly. The account fold and a save both run during the read. No native write
    may come before the read. Native is the base, as localStorage had no guest profile: every
    star is at least both copies', the settings are native's, and the two copies agree."""
    tag = "[%s] boot_save_cannot_clobber_native" % env.mode
    mid = _mid()
    fx = load_fixture(SAVES / "retired-account.json", "retired-account without a guest profile")
    del fx.local[GUEST], fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed(fx)
        ph.NATIVE.prefs[GUEST] = mid.local[GUEST]
        ph.NATIVE.get_delay_ms = 1500
        ph.launch("new", wait=False)
        ph.page.wait_for_function("typeof save === 'function' && typeof state === 'object'", timeout=10000)
        ph.page.evaluate(SAVE_JS)
        ph.boot()
        early = ph.NATIVE.writes_before_read(GUEST)
        if early:
            probs.append("%d native write(s) to %s before the launch had read it" % (len(early), GUEST))
        loc, _ = _agree(ph, probs, "after the restore,")
        for name, ref in (("v7-midway", mid.seed_states[GUEST]), ("S-0042", fx.seed_states[ACCOUNT_KEY])):
            low = [p for p, v in _starred(ref).items() if guarded(loc or {}).get(p, 0) < v]
            if low:
                probs.append("%d of %s's starred games are lower: %s" % (len(low), name, _clip(low, 2)))
        if _settings(loc) != _settings(mid.seed_states[GUEST]):
            probs.append("settings are %s, not the native copy's %s" % (json.dumps(_settings(loc)), json.dumps(_settings(mid.seed_states[GUEST]))))

    return _rel("N8", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps),
                "no native write before the slow read; after it every star is at least both copies', the settings "
                "are the native copy's, and both copies agree")


def boot_save_on_empty_webview(env, sabotage=None, prelude=None):
    """N9. An empty WebView, the native copy holding v7-midway, read slowly, and a save while it is
    read. BOOT_STATE is taken before the first write, so the launch knows localStorage was empty,
    nothing reaches native before the read, and she gets all of v7-midway back, settings included."""
    tag = "[%s] boot_save_on_empty_webview" % env.mode
    mid = _mid()
    seed = mid.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed_local({})
        ph.NATIVE.prefs[GUEST] = mid.local[GUEST]
        ph.NATIVE.get_delay_ms = 1500
        ph.launch("new", wait=False)
        ph.page.wait_for_function("typeof save === 'function' && typeof state === 'object'", timeout=10000)
        ph.page.evaluate(SAVE_JS)
        ph.boot()
        first = ph.page.evaluate("() => window.__e2eFirstSet")
        if not first:
            probs.append("nothing was ever written to localStorage")
        elif not first.get("bootState"):
            probs.append("the first localStorage write (%s) came before BOOT_STATE was taken" % first.get("key"))
        if ph.page.evaluate("() => typeof localWasMissing === 'undefined' ? null : localWasMissing") is not True:
            probs.append("localWasMissing is not true although localStorage was empty")
        early = ph.NATIVE.writes_before_read(GUEST)
        if early:
            probs.append("%d native write(s) to %s before the launch had read it" % (len(early), GUEST))
        loc, _ = _agree(ph, probs, "after the restore,")
        probs.extend(lowered(seed, loc or {}, GUEST) + fields_lost(seed, loc or {}, GUEST))
        if _settings(loc) != _settings(seed):
            probs.append("settings are %s, not hers (%s)" % (json.dumps(_settings(loc)), json.dumps(_settings(seed))))

    return _rel("N9", tag, _full_phone_run(env.phone(sabotage=sabotage, prelude=FIRST_SET + (prelude or "")), steps),
                "BOOT_STATE came before the first write, nothing reached native before the slow read, and the empty "
                "WebView got all of v7-midway back, settings included")


MERGE_JS = r"""(a) => {
  const c = o => JSON.parse(JSON.stringify(o));
  const m = { m1: mergeSavedState(c(a), c(a), { sameLearner: true }), m2: mergeSavedState(c(a), c(a)) };
  if (typeof mergeCopy !== "function") return Object.assign(m, { missing: true });
  const erased = Object.assign(c(a), { progress: {}, coins: 0, eraseGen: 1 });
  const old = Object.assign(c(a), { eraseGen: 0 });
  return {
    m1: m.m1,
    m2: m.m2,
    localWins: mergeCopy(c(erased), c(old), false),
    otherWins: mergeCopy(c(old), c(erased), false),
    equal: mergeCopy(Object.assign(c(old), { coins: 1 }), c(old), false),
  };
}"""


def merge_rules(env, sabotage=None, prelude=None):
    """M1-M3 and W1, in the page. M1: a learner merged with herself is unchanged. M2: two learners
    (the account fold) still add their attempts. M3: the higher eraseGen wins outright, either
    way round, and equal ones merge to the higher values. W1: with no Capacitor at all,
    nativeRestore resolves false and nothing throws."""
    tag = "[%s] merge_rules" % env.mode
    fx = _mid()
    a = fx.seed_states[GUEST]
    out = {}

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        out["W1"] = [] if ph.boot_result.get("nativeRestore") is False else [
            "nativeRestore resolved %s, not false" % json.dumps(ph.boot_result.get("nativeRestore", "(missing)"))]
        out["r"] = ph.page.evaluate(MERGE_JS, a)

    crash = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
    r = out.get("r") or {"missing": True}
    m1 = [] if r.get("m1") == a else ["merged with herself, she changes: " + _clip(diff(a, r.get("m1")), 3)]
    att = _num(_dict(_dict(a.get("levelTests")).get("1")).get("attempts")) or 0
    got2 = _num(_dict(_dict(_dict(r.get("m2")).get("levelTests")).get("1")).get("attempts"))
    m2 = [] if got2 == 2 * att else ["two learners' attempts give %s, not %s" % (got2, 2 * att)]
    m3 = []
    if r.get("missing"):
        m3.append("the build has no mergeCopy")
    else:
        if guarded(r["localWins"]).get("coins") != 0 or _dict(r["localWins"]).get("eraseGen") != 1:
            m3.append("a local erase (eraseGen 1) did not win over an older native copy")
        if guarded(r["otherWins"]).get("coins") != 0 or _dict(r["otherWins"]).get("eraseGen") != 1:
            m3.append("a native erase (eraseGen 1) did not win over an older local copy")
        if guarded(r["equal"]).get("coins") != guarded(a).get("coins"):
            m3.append("equal eraseGen did not merge to the higher gems")
    return [(c, not (crash + p), "%s %s %s" % (tag, c, _clip(crash + p, 3) if (crash + p) else g)) for c, p, g in (
        ("M1", m1, "a learner merged with herself is unchanged"),
        ("M2", m2, "two learners merged still add their attempts"),
        ("M3", m3, "the higher eraseGen wins either way round, and equal ones merge to the higher values"),
        ("W1", out.get("W1", []), "with no Capacitor, nativeRestore resolved false and nothing threw"))]


def settings_survive_unread_native(env, sabotage=None, prelude=None):
    """N10. An empty WebView, and a native copy holding v7-midway with every setting her own
    (Hindi, dark, sound off, Female, Tara). The first launch never merges it:
    (a) its two reads fail, and she earns a star in a new lesson;
    (b) it is closed while its slow read is still out, after a boot save.
    Either way that launch wrote boot defaults to localStorage, and the next launch must still take
    the native copy as the base: her settings, every star it held, (a) the new star too, and both
    copies agreeing afterwards."""
    tag = "[%s] settings_survive_unread_native" % env.mode
    mid = _mid()
    seed = dict(mid.seed_states[GUEST], sound=False)
    (ck, lk), = _fresh_lessons(mid, 1)
    probs = []
    for case in ("(a)", "(b)"):
        def steps(ph, pr, case=case):
            ph.seed_local({})
            ph.NATIVE.prefs[GUEST] = compact(seed)
            if case == "(a)":
                ph.NATIVE.fail_gets = 2
                ph.launch("new")
                if ph.NATIVE.fail_gets:
                    pr.append("the launch did not try to read the native copy twice, so this proved nothing")
                    return
                ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
            else:
                ph.NATIVE.get_delay_ms = 4000
                ph.launch("new", wait=False)
                ph.page.wait_for_function("typeof save === 'function' && typeof state === 'object'", timeout=10000)
                ph.page.evaluate(SAVE_JS)
                ph.NATIVE.get_delay_ms = 0
            if _local(ph) is None:
                pr.append("the first launch wrote nothing to localStorage, so this proved nothing")
                return
            ph.launch("new")             # in (b), this closes the first launch with its read still out
            loc, _ = _agree(ph, pr, "on the next launch,")
            now = loc or {}
            if _settings(now) != _settings(seed):
                pr.append("on the next launch her settings are %s, not the native copy's %s"
                          % (json.dumps(_settings(now)), json.dumps(_settings(seed))))
            if now.get("sound") is not False:
                pr.append("on the next launch sound is %s, though she had turned it off" % json.dumps(now.get("sound")))
            low = lowered(seed, now, GUEST)
            if low:
                pr.append("the next launch lowered the native copy's stars: %s" % _clip(low, 3))
            if case == "(a)" and _learn_star(now, ck, lk) != 3:
                pr.append("the star earned in the launch that could not read the native copy, at %s/%s, is gone" % (ck, lk))

        got = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), steps)
        probs.extend("%s %s" % (case, p) for p in got)
    return _rel("N10", tag, probs,
                "after a launch that (a) could not read the native copy or (b) was closed before its read came "
                "back, the next launch took the native copy as the base: her settings, every star, and the new one")


for _name, _fn, _modes in (("mirror_follows_save", mirror_follows_save, ("native",)),
                           ("first_launch_seeds_mirror", first_launch_seeds_mirror, ("native",)),
                           ("empty_webview_restored", empty_webview_restored, ("native",)),
                           ("native_lower_never_wins", native_lower_never_wins, ("native",)),
                           ("relaunch_does_not_inflate", relaunch_does_not_inflate, ("native",)),
                           ("erase_stays_erased", erase_stays_erased, ("native",)),
                           ("erase_with_waiting_fold_and_stale_mirror", erase_with_waiting_fold_and_stale_mirror, ("native",)),
                           ("erase_refused_everywhere", erase_refused_everywhere, ("web", "native")),
                           ("full_storage_native_carries", full_storage_native_carries, ("native",)),
                           ("boot_save_cannot_clobber_native", boot_save_cannot_clobber_native, ("native",)),
                           ("boot_save_on_empty_webview", boot_save_on_empty_webview, ("native",)),
                           ("settings_survive_unread_native", settings_survive_unread_native, ("native",)),
                           ("merge_rules", merge_rules, ("web",))):
    def _t(env, report, _fn=_fn):
        for _code, ok, msg in _fn(env):
            report.check(ok, msg)
    test(_name, modes=_modes)(_t)


# ---------------------------------------------------------------- Task 5: her progress as a file -
# Rule 7: Save my progress hands her one small file, and Bring my progress takes one back in. The
# file holds one learner and no name. Bringing asks first, says how many stars the file holds,
# refuses anything that is not ours, and never lowers a thing (rule 2). Settings come from the
# file only for a learner with no progress yet (D5), and its eraseGen is ignored.

FILE_NAME_RX = re.compile(r"^bodhya-progress-\d{4}-\d{2}-\d{2}\.json$")
ENVELOPE_KEYS = {"kind", "v", "app", "savedAt", "state"}
# Without Web Share, the web build's Save is a download, which the harness can read.
NO_WEB_SHARE = ("/* e2e: no Web Share */(function(){try{Object.defineProperty(Navigator.prototype,'canShare',"
                "{value:function(){return false;},configurable:true});}catch(e){}})();")
# Web Share that keeps what it was handed, for F2 on the web.
KEEP_WEB_SHARE = ("/* e2e: Web Share that keeps the file */(function(){window.__e2eShared=null;"
                  "Object.defineProperty(Navigator.prototype,'canShare',{value:function(d){return !!(d&&d.files);},configurable:true});"
                  "Object.defineProperty(Navigator.prototype,'share',{value:function(d){var f=d.files[0];"
                  "return f.text().then(function(t){window.__e2eShared={name:f.name,type:f.type,text:t};});},configurable:true});})();")
WARN_JS = r"""() => { const w = document.getElementById("progWarn");
  return w && w.style.display !== "none" ? { msg: w.dataset.msg || "", text: w.innerText } : null; }"""


def _file_phone(env, sabotage=None, prelude=None, share=NO_WEB_SHARE):
    ph = env.phone(sabotage=sabotage, prelude=prelude)
    if ph.NATIVE is None and share:
        ph.ctx.add_init_script(share)
    return ph


def _envelope(st):
    return {"kind": "bodhya-progress", "v": 1, "app": APP_ID, "savedAt": SAVED_AT, "state": st}


def _stars_in(st):
    return sum(_num(v) or 0 for ls in _dict(_dict(st).get("progress")).values()
               for g in _dict(ls).values() for v in _dict(g).values())


def _open_progress(page):
    if page.query_selector("#progWrap"):
        return
    page.click("#menuBtn", timeout=3000)
    page.click("#mProg", timeout=3000)
    page.wait_for_selector("#progWrap", timeout=3000)


def _save_file(ph):
    """Save my progress as she does it. Returns (file name, file text). On the web it is the
    download; in the app, the file that went to Filesystem.writeFile and then to Share."""
    _open_progress(ph.page)
    if ph.NATIVE is None:
        with ph.page.expect_download(timeout=5000) as dl:
            ph.page.click("#progSave", timeout=3000)
        d = dl.value
        with open(d.path(), encoding="utf-8") as f:
            return d.suggested_filename, f.read()
    n0 = len(ph.NATIVE.shares)
    ph.page.click("#progSave", timeout=3000)
    end = time.monotonic() + 5
    while len(ph.NATIVE.shares) == n0 and time.monotonic() < end:
        ph.page.wait_for_timeout(100)
    if len(ph.NATIVE.shares) == n0:
        raise FlowError("Save my progress never reached Share.share", ph.errors, False)
    uri = (ph.NATIVE.shares[-1].get("files") or [""])[0]
    f = next((x for x in ph.NATIVE.files.values() if x["uri"] == uri), None)
    if f is None:
        raise FlowError("Share.share got %s, which Filesystem.writeFile never wrote" % uri, ph.errors, False)
    return f["path"], f["data"]


def _bring(ph, data, name="bodhya-progress-2026-11-05.json", mime="application/json", answer="yes"):
    """Bring my progress as she does it: the button, the picker, the file, then her answer.
    Returns (what the confirm sheet said or None, the message the progress sheet then shows)."""
    _open_progress(ph.page)
    with ph.page.expect_file_chooser(timeout=3000) as fc:
        ph.page.click("#progBring", timeout=3000)
    buf = data if isinstance(data, bytes) else data.encode("utf-8")
    fc.value.set_files(files=[{"name": name, "mimeType": mime, "buffer": buf}])
    ph.page.wait_for_function("() => !!document.getElementById('bringWrap') || !!(%s)()" % WARN_JS, timeout=8000)
    asked = ph.page.evaluate("() => { const w = document.getElementById('bringWrap'); return w ? w.innerText : null; }")
    if asked is not None and answer:
        ph.page.click("#bringYes" if answer == "yes" else "#bringNo", timeout=3000)
        ph.page.wait_for_selector("#bringWrap", state="detached", timeout=8000)
        if answer == "yes":
            ph.page.wait_for_function("() => !!(%s)()" % WARN_JS, timeout=8000)
    return asked, ph.page.evaluate(WARN_JS)


def _post_erase():
    fx = load_fixture(SAVES / "post-erase.json", "post-erase")
    return fx, fx.seed_states[GUEST]


def _raised(st, ck, lk):
    """st with one more played lesson: three stars on `learn` at ck/lk."""
    out = json.loads(compact(st))
    out.setdefault("progress", {}).setdefault(ck, {}).setdefault(lk, {})["learn"] = 3
    return out


def save_bring_round_trip(env, sabotage=None, prelude=None):
    """F1. Save on one phone, bring the file onto another with nothing played: the file's state is
    hers exactly, the confirm sheet says its stars, and the second phone ends up as the first.
    F1b. The file holds one learner and no name, on the phone where S-0042's account was retired."""
    tag = "[%s] save_bring_round_trip" % env.mode
    fx = _mid()
    out = {"F1": [], "F1b": []}
    got = {}

    def first(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        name, text = _save_file(ph)
        got.update(name=name, text=text, local=_parsed(_local(ph)))

    out["F1"] += _full_phone_run(_file_phone(env, sabotage, prelude), first)
    if "text" in got:
        doc = json.loads(got["text"])
        if _dict(doc).get("state") != got["local"]:
            out["F1"].append("the file's state is not hers: %s" % _clip(diff(got["local"], _dict(doc).get("state"), values=False), 3))
        empty, _ = _post_erase()

        def second(ph, probs):
            ph.seed(empty)
            ph.launch("new")
            asked, said = _bring(ph, got["text"], name=got["name"])
            want = _stars_in(doc["state"])
            if asked is None:
                probs.append("the confirm sheet never came (%s)" % (said or {}).get("msg"))
                return
            if not re.search(r"(^|\D)%d(\D|$)" % want, asked):
                probs.append("the confirm sheet does not say the file's %d stars: %r" % (want, _line(asked, 120)))
            if (said or {}).get("msg") != "brought":
                probs.append("after yes the sheet said %r, not that it was brought back" % (said,))
            now = _parsed(_local(ph))
            ch = changed_numbers(got["local"], now or {})
            if ch:
                probs.append("the second phone differs from the first: %s" % _clip(["%s %s -> %s" % (p, a, b) for p, (a, b) in sorted(ch.items())], 3))
            if _settings(now) != _settings(got["local"]):
                probs.append("the second phone, with no progress, kept settings %s, not the file's %s" % (_settings(now), _settings(got["local"])))

        out["F1"] += _full_phone_run(_file_phone(env, sabotage, prelude), second)

    retired = load_fixture(SAVES / "retired-account.json", "retired-account")
    held = {}

    def third(ph, probs):
        ph.seed(retired)
        ph.launch("new")
        name, text = _save_file(ph)
        held.update(name=name, text=text, local=_parsed(_local(ph)))

    out["F1b"] += _full_phone_run(_file_phone(env, sabotage, prelude), third)
    if "text" in held:
        if not FILE_NAME_RX.match(held["name"] or ""):
            out["F1b"].append("the file is called %r" % held["name"])
        try:
            doc = json.loads(held["text"])
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or set(doc) != ENVELOPE_KEYS:
            out["F1b"].append("the file's keys are %s, not the envelope's" % (sorted(doc) if isinstance(doc, dict) else type(doc).__name__))
        else:
            if (doc["kind"], doc["v"], doc["app"]) != ("bodhya-progress", 1, APP_ID):
                out["F1b"].append("the envelope says %r" % ((doc["kind"], doc["v"], doc["app"]),))
            if doc["state"] != held["local"]:
                out["F1b"].append("the file's state is not the guest profile on screen")
        for bad in ("S-0042", "Test Learner", "hikmat_"):
            if bad in held["text"]:
                out["F1b"].append("the file holds %r" % bad)
    return [(c, not p, "%s %s %s" % (tag, c, _clip(p, 4) if p else g)) for c, p, g in (
        ("F1", out["F1"], "the file is her state exactly, the confirm sheet said its stars, and the phone with nothing "
                          "played became her, settings included"),
        ("F1b", out["F1b"], "the file is the envelope with one learner in it, no name, no id, no other profile, "
                            "and a name with only the date in it"))]


def save_hands_over_file(env, sabotage=None, prelude=None):
    """F2. In the app, Save writes the file to the cache directory and hands that file to Share; a
    share she cancels is not reported as saved. On the web with Web Share, the shared file is the
    same envelope under the same name."""
    tag = "[%s] save_hands_over_file" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        _open_progress(ph.page)
        if ph.NATIVE is None:
            ph.page.click("#progSave", timeout=3000)
            ph.page.wait_for_function("() => !!window.__e2eShared", timeout=5000)
            sh = ph.page.evaluate("() => window.__e2eShared")
            if not FILE_NAME_RX.match(sh["name"] or ""):
                probs.append("Web Share got a file called %r" % sh["name"])
            if _dict(json.loads(sh["text"])).get("state") != _parsed(_local(ph)):
                probs.append("Web Share got a file whose state is not hers")
        else:
            name, text = _save_file(ph)
            f = ph.NATIVE.files.get(name, {})
            if f.get("directory") != "CACHE" or not FILE_NAME_RX.match(name):
                probs.append("writeFile wrote %r to %r" % (name, f.get("directory")))
            if _dict(json.loads(text)).get("state") != _parsed(_local(ph)):
                probs.append("the shared file's state is not hers")
            ph.page.wait_for_function("() => !!(%s)()" % WARN_JS, timeout=5000)
            if (ph.page.evaluate(WARN_JS) or {}).get("msg") != "saved":
                probs.append("after the share the sheet said %r, not that the file was saved" % (ph.page.evaluate(WARN_JS),))
            ph.page.evaluate("() => { const w = document.getElementById('progWarn'); w.style.display = 'none'; w.dataset.msg = ''; }")
            ph.NATIVE.cancel_next_share = True
            n0 = len(ph.NATIVE.log)
            ph.page.click("#progSave", timeout=3000)
            end = time.monotonic() + 5
            while not any(e.get("op") == "share" and e.get("canceled") for e in ph.NATIVE.log[n0:]) and time.monotonic() < end:
                ph.page.wait_for_timeout(100)
            ph.page.wait_for_timeout(500)
            w = ph.page.evaluate(WARN_JS)
            if w and w.get("msg") == "saved":
                probs.append("a share she cancelled was reported as saved")

    good = ("the file went to the cache directory, then to Share; a cancelled share was not called saved"
            if env.mode == "native" else "Web Share got her envelope under the dated name")
    return _rel("F2", tag, _full_phone_run(_file_phone(env, sabotage, prelude, share=KEEP_WEB_SHARE), steps), good)


def bring_asks_and_follows_d5(env, sabotage=None, prelude=None):
    """F1c. Not now changes nothing, in either copy. F1d. On a phone with progress the file's
    settings are not taken; on one with none they are. The file's eraseGen 7 is ignored in both."""
    tag = "[%s] bring_asks_and_follows_d5" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]
    (ck, lk), = _fresh_lessons(fx, 1)
    other = dict(_raised(seed, ck, lk), lang="en", theme="light", sound=False, gender="Male", mascot="kabir", eraseGen=7)
    text = compact(_envelope(other))
    out = {"F1c": [], "F1d": []}

    def ask(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        if ph.NATIVE is not None:
            _settled(ph)
        before, nat0 = _local(ph), (ph.NATIVE.prefs.get(GUEST) if ph.NATIVE else None)
        asked, said = _bring(ph, text, answer="no")
        if asked is None:
            probs.append("it merged without asking (%s)" % ((said or {}).get("msg"),))
            return
        if not re.search(r"(^|\D)%d(\D|$)" % _stars_in(other), asked):
            probs.append("the confirm sheet does not say the file's %d stars" % _stars_in(other))
        ph.page.wait_for_timeout(600)
        if _local(ph) != before:
            probs.append("Not now changed localStorage: %s" % _clip(diff(_parsed(before), _parsed(_local(ph)), values=False), 3))
        if ph.NATIVE is not None and ph.NATIVE.prefs.get(GUEST) != nat0:
            probs.append("Not now changed the native copy")

    out["F1c"] += _full_phone_run(_file_phone(env, sabotage, prelude), ask)

    def has_progress(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        asked, said = _bring(ph, text)
        now = _parsed(_local(ph))
        if _learn_star(now, ck, lk) != 3:   # whether it asked first is F1c's to judge
            probs.append("with progress: the file's new lesson did not come in (%s)" % ((said or {}).get("msg"),))
        if _settings(now) != _settings(seed):
            probs.append("with progress: her settings became %s" % _settings(now))
        if _dict(now).get("eraseGen", 0) != _dict(seed).get("eraseGen", 0):
            probs.append("with progress: eraseGen became %s, the file's" % _dict(now).get("eraseGen"))

    def no_progress(ph, probs):
        empty, est = _post_erase()
        ph.seed(empty)
        ph.launch("new")
        asked, said = _bring(ph, text)
        now = _parsed(_local(ph))
        if _learn_star(now, ck, lk) != 3:
            probs.append("with no progress: the file did not come in (%s)" % ((said or {}).get("msg"),))
        if _settings(now) != _settings(other):
            probs.append("with no progress: settings are %s, not the file's %s" % (_settings(now), _settings(other)))
        if _dict(now).get("eraseGen", 0) != _dict(est).get("eraseGen", 0):
            probs.append("with no progress: eraseGen became %s, the file's" % _dict(now).get("eraseGen"))

    out["F1d"] += _full_phone_run(_file_phone(env, sabotage, prelude), has_progress)
    out["F1d"] += _full_phone_run(_file_phone(env, sabotage, prelude), no_progress)
    return [(c, not p, "%s %s %s" % (tag, c, _clip(p, 4) if p else g)) for c, p, g in (
        ("F1c", out["F1c"], "the confirm sheet said the file's stars, and Not now left both copies as they were"),
        ("F1d", out["F1d"], "her settings stayed on a phone with progress, the file's came on one with none, "
                            "and the file's eraseGen 7 was ignored on both"))]


def bring_never_lowers(env, sabotage=None, prelude=None):
    """F3. A file with fewer stars and gems lowers nothing. Her own file, brought twice, changes
    nothing the second time: attempts, gems and seen-lists do not add up."""
    tag = "[%s] bring_never_lowers" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        _bring(ph, compact(_envelope(_lowered_stars(seed))))
        probs.extend(sorted(lowered(seed, _parsed(_local(ph)), GUEST), key=lambda x: " coins " not in x))
        if probs:
            return
        own = compact(_envelope(seed))
        _bring(ph, own)
        once = _parsed(_local(ph))
        _bring(ph, own)
        twice = _parsed(_local(ph))
        ch = changed_numbers(once, twice)
        if ch or once != twice:
            probs.append("her own file, brought a second time, changed %s" % _clip(
                ["%s %s -> %s" % (p, a, b) for p, (a, b) in sorted(ch.items())] or diff(once, twice, values=False), 3))
        if ph.NATIVE is not None:
            _agree(ph, probs, "after bringing,")

    return _rel("F3", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "an older file lowered no star or gem, and her own file brought twice changed nothing")


EVIL_STATE = r"""{"__proto__":{"polluted":"state"},"constructor":{"prototype":{"polluted":"ctor"}},
 "progress":{"__proto__":{"polluted":"progress"},"eng-foundation":{"l01":{"learn":99,"listen":-1,"sounds":"3",
   "rhyme":2.6,"__proto__":3,"PAD":3}},"e2e-new":{"l01":{"learn":"NaN","listen":1e309}}},
 "coins":1e12,"levelTests":{"1":{"passed":"yes","bestPct":250,"attempts":-4},"__proto__":{"passed":true}},
 "testSeen":{"l1":["q1",7,{"x":1},"q2"]},"seenTypes":{"tap":1,"__proto__":1},"lang":"fr","theme":"neon",
 "sound":"loud","gender":"Robot","mascot":"nobody","eraseGen":99}"""


def _forbidden_keys(x, path=""):
    out = []
    if isinstance(x, dict):
        for k, v in x.items():
            if k in ("__proto__", "constructor", "prototype") or len(k) > 140:
                out.append("%s%s" % (path, k[:20]))
            out.extend(_forbidden_keys(v, path + k[:20] + "."))
    elif isinstance(x, list):
        for v in x:
            out.extend(_forbidden_keys(v, path))
    return out


def _bad_stars(st):
    return ["%s/%s/%s=%r" % (ck, lk, a, v) for ck, ls in _dict(_dict(st).get("progress")).items()
            for lk, g in _dict(ls).items() for a, v in _dict(g).items()
            if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3)]


def bring_rejects_bad_files(env, sabotage=None, prelude=None):
    """F4. What is not ours is refused without touching her progress: a photo, cut-off JSON,
    another app's JSON, the wrong kind, a 3 MB file. A file that is ours but poisoned (__proto__,
    constructor, stars of 99, -1 and "3", gems of 10^12, a 200-character key) is cleaned: nothing
    pollutes Object.prototype, every star is 0-3, nothing of hers goes down."""
    tag = "[%s] bring_rejects_bad_files" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]
    good = compact(_envelope(seed))
    big = compact(_envelope(dict(seed, testSeen={"pad": ["q%07d" % i for i in range(330000)]})))
    refused = [("a photo", "photo.jpg", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40, "image/png"),
               ("cut-off JSON", "bodhya-progress-2026-11-05.json", good[:len(good) // 2], "application/json"),
               ("another app's JSON", "data.json", '{"foo":1}', "application/json"),
               ("the wrong kind", "x.json", compact(dict(_envelope(seed), kind="other-progress")), "application/json"),
               ("a 3 MB file", "bodhya-progress-2026-11-05.json", big, "application/json")]

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        if len(big) < 3 * 1024 * 1024:
            probs.append("the harness's big file is only %d bytes" % len(big))
        for what, name, data, mime in refused:
            before = _local(ph)
            asked, said = _bring(ph, data, name=name, mime=mime)
            if asked is not None:
                probs.append("%s got the confirm sheet" % what)
                ph.page.click("#bringNo", timeout=3000)
            elif (said or {}).get("msg") != "notours":
                probs.append("%s was answered with %r" % (what, said))
            if _local(ph) != before:
                probs.append("%s changed her stored state" % what)
        evil = '{"kind":"bodhya-progress","v":1,"app":"%s","savedAt":"%s","state":%s}' % (APP_ID, SAVED_AT, EVIL_STATE.replace("PAD", "k" * 200))
        try:
            asked, said = _bring(ph, evil)
            if asked is None:
                probs.append("the poisoned file was refused (%r), not cleaned" % (said,))
        except PWTimeout:
            # Still look at what it did: a poisoned merge can stall after it has polluted.
            probs.append("the poisoned file got no answer")
        now = _parsed(_local(ph))
        polluted = ph.page.evaluate("() => [({}).polluted, Object.prototype.polluted, Object.getPrototypeOf(state) === Object.prototype]")
        if polluted[0] is not None or polluted[1] is not None or not polluted[2]:
            probs.append("Object.prototype was polluted: %r" % (polluted,))
        bad = _forbidden_keys(now) + _bad_stars(now)
        if bad:
            probs.append("the stored state holds %s" % _clip(bad, 4))
        probs.extend(lowered(seed, now, GUEST))
        if (_num(_dict(now).get("coins")) or 0) > 10000000:
            probs.append("gems became %s" % _dict(now).get("coins"))
        if _settings(now) != _settings(seed) or _dict(now).get("eraseGen", 0) != 0:
            probs.append("the poisoned file changed her settings or eraseGen")
        if ph.NATIVE is not None:
            _agree(ph, probs, "after the poisoned file,")

    return _rel("F4", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "a photo, cut-off JSON, another app's JSON, the wrong kind and a 3 MB file were refused untouched, "
                "and a poisoned file was cleaned: no pollution, every star 0-3, nothing lowered")


def bring_after_erase(env, sabotage=None, prelude=None):
    """F5. She saves her file, erases, and brings the file back: every star returns, and stays for
    two launches, with the mirror on in the app."""
    tag = "[%s] bring_after_erase" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        _, text = _save_file(ph)
        ph.page.click("#dClose", timeout=3000)
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        asked, said = _bring(ph, text)
        if asked is None:
            probs.append("after the erase the file was not taken (%r)" % (said,))
            return
        for which in ("right after", "after a launch", "after another launch"):
            if which != "right after":
                ph.launch("new")
            loc = _parsed(_local(ph))
            low = lowered(seed, loc, GUEST)
            if low:
                probs.append("%s: %s" % (which, _clip(low, 3)))
                break
            if ph.NATIVE is not None:
                _agree(ph, probs, which + ",")

    return _rel("F5", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "a file saved before an erase brought every star back, and they stayed for two launches")


def bring_any_name_or_type(env, sabotage=None, prelude=None):
    """F6. The file as pickers hand it over after WhatsApp or Bluetooth: no type, octet-stream, a
    ' (1)' in the name, no extension. Each is brought in."""
    tag = "[%s] bring_any_name_or_type" % env.mode
    fx = _mid()
    seed = fx.seed_states[GUEST]
    lessons = _fresh_lessons(fx, 3)
    kinds = [("bodhya-progress-2026-11-05 (1).json", "application/octet-stream"),
             ("bodhya-progress-2026-11-05", ""),
             ("DOC-20261105-WA0003.json", "text/plain")]

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        for (name, mime), (ck, lk) in zip(kinds, lessons):
            asked, said = _bring(ph, compact(_envelope(_raised(seed, ck, lk))), name=name, mime=mime)
            if asked is None or _learn_star(_parsed(_local(ph)), ck, lk) != 3:
                probs.append("%r as %r was not brought in (%r)" % (name, mime or "no type", said))

    def old_webview(ph, probs):
        # A WebView from before Chrome 76 has no File.text(); the file must still come in.
        ph.ctx.add_init_script("/* e2e: a WebView older than Chrome 76 */delete Blob.prototype.text;")
        ph.seed(fx)
        ph.launch("new")
        (ck, lk) = lessons[0]
        asked, said = _bring(ph, compact(_envelope(_raised(seed, ck, lk))))
        if asked is None or _learn_star(_parsed(_local(ph)), ck, lk) != 3:
            probs.append("with no File.text(), as before Chrome 76, the file was not brought in (%r)" % (said,))

    probs = _full_phone_run(_file_phone(env, sabotage, prelude), steps)
    probs += _full_phone_run(_file_phone(env, sabotage, prelude), old_webview)
    return _rel("F6", tag, probs,
                "files with no type, octet-stream, a ' (1)' name, no extension or a WhatsApp name were all brought in, "
                "also by a WebView with no File.text()")


# Names every object inherits, as the keys of a file. Under any of them the merge would find the
# built-in member instead of an empty place: it writes the file's games onto the built-in, or, for
# a seen-question list, calls the built-in as if it were one and throws.
INHERITED_FILE_STATE = {
    "progress": {"toString": {"l01": {"learn": 3}}, "hasOwnProperty": {"x": {"y": 3}},
                 "valueOf": {"l01": {"learn": 3}}},
    "levelTests": {"valueOf": {"passed": True, "bestPct": 100, "attempts": 1}},
    "seenTypes": {"isPrototypeOf": 1}, "lastLoc": {"hasOwnProperty": {"ck": "a", "lk": "b"}}, "coins": 0}
INHERITED_SEEN_STATE = {"testSeen": {"toString": ["q1"], "valueOf": ["q2"]}, "coins": 0}
PROTO_JS = r"""() => {
  const names = ["toString", "hasOwnProperty", "valueOf", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString"];
  const onBuiltins = names.filter(n => Object.keys(Object.prototype[n]).length > 0);
  const own = [];
  for (const f of ["progress", "levelTests", "testSeen", "seenTypes", "lastLoc", "videosSeen"])
    for (const n of names) if (state[f] && Object.prototype.hasOwnProperty.call(state[f], n)) own.push(f + "." + n);
  return { onBuiltins, own, works: typeof ({}).hasOwnProperty === "function" && String({}) === "[object Object]" };
}"""


def bring_ignores_inherited_names(env, sabotage=None, prelude=None):
    """F8. A file whose keys are names every object inherits (toString, hasOwnProperty, valueOf,
    isPrototypeOf) is cleaned of them: nothing is written onto a built-in, her state holds no such
    key, the built-ins still work, and nothing of hers goes down."""
    tag = "[%s] bring_ignores_inherited_names" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        # One file at a time: the second makes a build without the fix throw, and what the first
        # found must be on record before that.
        for what, st in (("games, tests, coaching and resume points", INHERITED_FILE_STATE),
                         ("seen-question lists", INHERITED_SEEN_STATE)):
            asked, said = _bring(ph, compact(_envelope(st)))
            if asked is None:
                probs.append("the file of %s never reached the confirm sheet (%r), so this proved nothing" % (what, said))
                return
            r = ph.page.evaluate(PROTO_JS)
            if r["onBuiltins"]:
                probs.append("the file of %s wrote onto Object.prototype.%s"
                             % (what, ", Object.prototype.".join(r["onBuiltins"])))
            if r["own"]:
                probs.append("after the file of %s her state holds %s" % (what, ", ".join(r["own"])))
            if not r["works"]:
                probs.append("after the file of %s a built-in no longer works" % what)
        probs.extend(lowered(pre, _parsed(_local(ph)), GUEST))

    return _rel("F8", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "a file keyed toString, hasOwnProperty, valueOf and isPrototypeOf wrote nothing onto a built-in, "
                "left no such key in her state, and lowered nothing")


for _name, _fn in (("save_bring_round_trip", save_bring_round_trip),
                   ("save_hands_over_file", save_hands_over_file),
                   ("bring_asks_and_follows_d5", bring_asks_and_follows_d5),
                   ("bring_never_lowers", bring_never_lowers),
                   ("bring_rejects_bad_files", bring_rejects_bad_files),
                   ("bring_after_erase", bring_after_erase),
                   ("bring_any_name_or_type", bring_any_name_or_type),
                   ("bring_ignores_inherited_names", bring_ignores_inherited_names)):
    def _t(env, report, _fn=_fn):
        for _code, ok, msg in _fn(env):
            report.check(ok, msg)
    test(_name)(_t)


# ---------------------------------------------------------------- Task 6: the confirm sheet and undo -
# Rule 11: Start fresh asks on its own sheet, where Keep it, not Erase, lies under the thumb that
# tapped Start fresh, and Erase ignores taps for 600 ms. An erase keeps an undo copy for 7 days, in
# localStorage and natively (D7); restoring it brings every star back, keeps what she earned since,
# and leaves eraseGen one above both, so no copy from before it can win.

UNDO = "hikmat_undo_guest"
DAY_MS = 86400000
VIEWPORTS = ({"width": 360, "height": 640}, {"width": 393, "height": 852}, {"width": 1280, "height": 800})
RECT_JS = r"""(id) => { const r = document.getElementById(id).getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height }; }"""
UNDO_ROW_JS = r"""() => { const b = document.getElementById("progUndo");
  return b ? { days: Number(b.dataset.days), text: b.innerText } : null; }"""
CLOCK_JS = "/* e2e: the clock is %d days on */(function(){var n=Date.now.bind(Date);Date.now=function(){return n()+%d;};})();"


def _undo_row(ph, wait_s=3.0):
    """The "Bring back what I erased" row in My progress, or None. It is drawn once readUndo answers."""
    _open_progress(ph.page)
    try:
        ph.page.wait_for_selector("#progUndo", timeout=int(wait_s * 1000))
    except PWTimeout:
        return None
    return ph.page.evaluate(UNDO_ROW_JS)


def _restore(ph):
    """Bring back what I erased, as she does it. Returns the message the sheet then shows."""
    _open_progress(ph.page)
    ph.page.click("#progUndo", timeout=5000)
    ph.page.wait_for_function("() => ['restored', 'undofail'].includes((%s)())" % SHEET_MSG_JS, timeout=15000)
    return ph.page.evaluate(SHEET_MSG_JS)


def _undo_copies(ph):
    """{where: parsed undo copy} for each store that holds one."""
    out = {}
    if _local(ph, UNDO) is not None:
        out["localStorage"] = _parsed(_local(ph, UNDO))
    if ph.NATIVE is not None and UNDO in ph.NATIVE.prefs:
        out["native"] = _parsed(ph.NATIVE.prefs[UNDO])
    return out


def _outside(a, b, grow):
    """True when rect a does not touch rect b grown by `grow` px on every side."""
    return (a["x"] + a["w"] <= b["x"] - grow or a["x"] >= b["x"] + b["w"] + grow
            or a["y"] + a["h"] <= b["y"] - grow or a["y"] >= b["y"] + b["h"] + grow)


def erase_confirm_layout(env, sabotage=None, prelude=None):
    """U1. At 360x640, 393x852 and 1280x800, in English and Hindi: Erase is nowhere near where
    Start fresh was (its rect grown by 16 px), Keep it covers Start fresh's centre, Keep it leaves
    My progress open, and a real double tap on Start fresh (120 ms apart) erases nothing."""
    tag = "[%s] erase_confirm_layout" % env.mode
    seed = _mid().seed_states[GUEST]
    probs = []
    for vp in VIEWPORTS:
        for lang in ("en", "hi"):
            where = "%dx%d %s" % (vp["width"], vp["height"], lang)
            fx = Fixture("v7-midway in " + lang, _envelope(dict(seed, lang=lang)))

            def steps(ph, p, fx=fx, where=where, vp=vp):
                ph.seed(fx)
                ph.launch("new")
                ph.page.set_viewport_size(vp)
                _open_progress(ph.page)
                ph.page.evaluate("() => document.getElementById('progWipe').scrollIntoView({ block: 'end' })")
                ph.page.wait_for_timeout(400)
                w = ph.page.evaluate(RECT_JS, "progWipe")
                before = _local(ph)
                ph.page.click("#progWipe", timeout=3000)
                ph.page.wait_for_selector("#eraseWrap", timeout=3000)
                ph.page.wait_for_timeout(400)
                y, k = ph.page.evaluate(RECT_JS, "eraseYes"), ph.page.evaluate(RECT_JS, "eraseKeep")
                cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
                if not _outside(y, w, 16):
                    p.append("%s: Erase lies within 16 px of where Start fresh was" % where)
                if not (k["x"] <= cx <= k["x"] + k["w"] and k["y"] <= cy <= k["y"] + k["h"]):
                    p.append("%s: Keep it (y %d-%d) does not cover Start fresh's centre (%d, %d)"
                             % (where, k["y"], k["y"] + k["h"], cx, cy))
                ph.page.click("#eraseKeep", timeout=3000)
                ph.page.wait_for_selector("#eraseWrap", state="detached", timeout=3000)
                if not ph.page.query_selector("#progWrap"):
                    p.append("%s: Keep it closed My progress as well" % where)
                ph.page.mouse.click(cx, cy)
                ph.page.wait_for_timeout(120)
                ph.page.mouse.click(cx, cy)
                ph.page.wait_for_timeout(1500)
                if _local(ph) != before:
                    p.append("%s: a double tap on Start fresh changed her progress" % where)

            probs += _full_phone_run(_file_phone(env, sabotage, prelude), steps)
    return _rel("U1", tag, probs, "at 360x640, 393x852 and 1280x800 in English and Hindi, Keep it lay under Start "
                                  "fresh's centre, Erase stayed 16 px clear, and a double tap erased nothing")


def erase_guard(env, sabotage=None, prelude=None):
    """U2. A tap on Erase as the sheet opens does nothing; one after 600 ms erases."""
    tag = "[%s] erase_guard" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        before = _local(ph)
        _open_erase(ph.page)
        ph.page.evaluate("() => document.getElementById('eraseYes').click()")
        ph.page.wait_for_timeout(300)
        if _local(ph) != before or not ph.page.query_selector("#eraseWrap"):
            probs.append("a tap on Erase as the sheet opened was taken")
            return
        ph.page.wait_for_timeout(500)
        ph.page.click("#eraseYes", timeout=3000)
        try:
            ph.page.wait_for_function("(m) => (%s)() === m" % SHEET_MSG_JS, arg="erased", timeout=15000)
        except PWTimeout:
            probs.append("a tap on Erase after 600 ms did not erase (%r)" % ph.page.evaluate(SHEET_MSG_JS))
            return
        if ph.page.evaluate(SHOWN_JS)["total"]:
            probs.append("after the erase she still has stars")

    return _rel("U2", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "a tap on Erase as the sheet opened did nothing, and one after 600 ms erased")


def undo_round_trip(env, sabotage=None, prelude=None):
    """U3. Erase: the undo copy is in localStorage (and natively in the app), her state as it was,
    until 7 days after erasedAt. A relaunch shows the undo row with 7 days left. Restoring gives
    back the state before the erase, with eraseGen 2 and no undo copy left, and so does the next
    launch, which has no undo row."""
    tag = "[%s] undo_round_trip" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre, pre_snap = _parsed(_local(ph)), ph.snap()
        t0 = time.time() * 1000
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        copies = _undo_copies(ph)
        for where in ("localStorage",) + (("native",) if ph.NATIVE is not None else ()):
            u = copies.get(where)
            if u is None:
                probs.append("there is no undo copy in %s" % where)
                continue
            if (u.get("kind"), u.get("v")) != ("bodhya-erase-undo", 1):
                probs.append("the undo copy in %s is %r, not bodhya-erase-undo v1" % (where, (u.get("kind"), u.get("v"))))
            at, until = _num(u.get("erasedAt")) or 0, _num(u.get("until")) or 0
            if until - at != 7 * DAY_MS or abs(at - t0) > 60000:
                probs.append("the undo copy in %s runs from %s to %s, not 7 days from the erase" % (where, at, until))
            if u.get("state") != pre:
                probs.append("the undo copy in %s is not her state before the erase: %s"
                             % (where, _clip(diff(pre, u.get("state"), values=False), 3)))
        if probs:
            return
        ph.launch("new")
        row = _undo_row(ph)
        if not row or row["days"] != 7:
            probs.append("after a relaunch the undo row is %r, not one with 7 days left" % (row,))
            return
        msg = _restore(ph)
        if msg != "restored":
            probs.append("Bring back what I erased ended with %r" % msg)
            return
        for which in ("right after", "after a relaunch"):
            if which != "right after":
                ph.launch("new")
            now = _parsed(_local(ph))
            low = lowered(pre, now, GUEST) + fields_lost(pre, now, GUEST)
            ch = changed_numbers(pre, now or {})
            if low or ch:
                probs.append("%s: %s" % (which, _clip(low + ["%s %s -> %s" % (q, a, b) for q, (a, b) in sorted(ch.items())], 3)))
            if _dict(now).get("eraseGen") != 2:
                probs.append("%s: eraseGen is %s, not 2" % (which, _dict(now).get("eraseGen")))
            if _undo_copies(ph):
                probs.append("%s: an undo copy is still there (%s)" % (which, ", ".join(_undo_copies(ph))))
            snap = ph.snap()
            if snap["level"] != pre_snap["level"] or sorted(snap["playedOpen"]) != sorted(pre_snap["playedOpen"]):
                probs.append("%s: level %s and %d open played lessons, not %s and %d" % (
                    which, snap["level"], len(snap["playedOpen"]), pre_snap["level"], len(pre_snap["playedOpen"])))
        if _undo_row(ph, wait_s=1.5) is not None:
            probs.append("after the restore and a relaunch the undo row is still there")

    return _rel("U3", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "the erase kept a 7-day undo copy of her state, the row showed 7 days left after a relaunch, and the "
                "restore gave back every star, level and open lesson with eraseGen 2 and no copy left")


def undo_survives_webview_wipe(env, sabotage=None, prelude=None):
    """U4. Erase, then the WebView's data is lost. The undo row is read from the native copy, and
    the restore brings every star back."""
    tag = "[%s] undo_survives_webview_wipe" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        _settled(ph)
        ph.page.evaluate("() => localStorage.clear()")
        ph.launch("new")
        if _undo_row(ph) is None:
            probs.append("after the WebView was wiped there is no undo row: the native undo copy was not read")
            return
        msg = _restore(ph)
        probs.extend(lowered(pre, _parsed(_local(ph)), GUEST) if msg == "restored"
                     else ["the restore ended with %r" % msg])

    return _rel("U4", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "with the WebView wiped after the erase, the undo row came from the native copy and the restore "
                "brought every star back")


def undo_expires(env, sabotage=None, prelude=None):
    """U5. Eight days after the erase the undo copies are removed from both stores at launch, and
    there is no undo row. Six days after it, the row says it is the last day."""
    tag = "[%s] undo_expires" % env.mode
    fx = _mid()
    probs = []

    def after(days):
        def steps(ph, p):
            ph.seed(fx)
            ph.launch("new")
            why = start_fresh(ph.page)
            if why:
                p.append(why)
                return
            if ph.NATIVE is not None:
                _settled(ph)
            ph.ctx.add_init_script(CLOCK_JS % (days, days * DAY_MS))
            ph.launch("new")
            if days == 8:
                end = time.monotonic() + 3
                while _undo_copies(ph) and time.monotonic() < end:
                    ph.page.wait_for_timeout(100)
                if _undo_copies(ph):
                    p.append("8 days on, the undo copy is still in %s" % ", ".join(_undo_copies(ph)))
                if _undo_row(ph, wait_s=1.5) is not None:
                    p.append("8 days on, the undo row is still offered")
            else:
                row = _undo_row(ph)
                if not row or row["days"] != 1:
                    p.append("%d days on, the undo row is %r, not the last day" % (days, row))
        return steps

    for d in (8, 6):
        probs += _full_phone_run(_file_phone(env, sabotage, prelude), after(d))
    return _rel("U5", tag, probs, "8 days on, both undo copies were gone at launch with no row; 6 days on, the row "
                                  "said it was the last day")


def restore_beats_stale_native(env, sabotage=None, prelude=None):
    """U6. Every native write of the restore is lost (mirrorNow writes again after one lost write,
    so a single drop heals), and the native copy still holds the erased state. The restore has the
    higher eraseGen, so two launches keep every restored star."""
    tag = "[%s] restore_beats_stale_native" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        _settled(ph)
        ph.NATIVE.refuse_writes = True
        if _restore(ph) != "restored":
            probs.append("the restore did not finish")
            return
        ph.NATIVE.refuse_writes = False
        if _dict(_parsed(ph.NATIVE.prefs.get(GUEST))).get("eraseGen") != 1:
            probs.append("the native copy is not the erased one, so the check proved nothing")
            return
        for which in ("the next launch", "the launch after it"):
            ph.launch("new")
            low = lowered(pre, _parsed(_local(ph)), GUEST)
            if low:
                probs.append("%s, with the stale native copy from the erase: %s" % (which, _clip(low, 3)))
                break

    return _rel("U6", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "with every native write of the restore lost, two launches kept every restored star over the "
                "erased native copy")


def restore_keeps_new_play(env, sabotage=None, prelude=None):
    """U7. Erase, earn a star in a new lesson, restore: the old stars and the new one are all there."""
    tag = "[%s] restore_keeps_new_play" % env.mode
    fx = _mid()
    (ck, lk), = _fresh_lessons(fx, 1)

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        if _restore(ph) != "restored":
            probs.append("the restore did not finish")
            return
        now = _parsed(_local(ph))
        probs.extend(lowered(pre, now, GUEST))
        if _learn_star(now, ck, lk) != 3:
            probs.append("the star she earned after the erase, at %s/%s, is gone" % (ck, lk))

    return _rel("U7", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "the restore brought back every old star and kept the new one earned after the erase")


def rollback_after_erase_and_restore(env, sabotage=None, prelude=None):
    """U8. R after the erase and after the restore: v7 opens each state with no error, shows 0 stars
    after the erase, and after the restore the same total, level and open played lessons as before."""
    tag = "[%s] rollback_after_erase_and_restore" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = ph.snap()
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        if ph.NATIVE is not None:
            _settled(ph)
        ph.launch("old")
        old = ph.snap()
        if old["total"]:
            probs.append("v7 after the erase shows %s stars" % old["total"])
        ph.launch("new")
        if _restore(ph) != "restored":
            probs.append("the restore did not finish")
            return
        if ph.NATIVE is not None:
            _settled(ph)
        ph.launch("old")
        old = ph.snap()
        if old["total"] != pre["total"] or old["level"] != pre["level"]:
            probs.append("v7 after the restore shows %s stars at level %s, not %s at %s"
                         % (old["total"], old["level"], pre["total"], pre["level"]))
        shut = [x for x in pre["playedOpen"] if x not in old["playedOpen"]]
        if shut:
            probs.append("v7 after the restore shuts %d played lessons: %s" % (len(shut), _clip(map(str, shut), 3)))

    return _rel("U8", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "v7 opened the erased state with 0 stars, and the restored one with the same total, level and open "
                "lessons, with no error")


def undo_brings_back_waiting_fold(env, sabotage=None, prelude=None):
    """U9. The S5 phone: S-0042's fold waits for room, and Start fresh ends it. The undo copy names
    that account (never by name), and the restore brings its stars back as the fold would have
    landed them, once: two more launches neither lose them nor count its attempts again."""
    tag = "[%s] undo_brings_back_waiting_fold" % env.mode
    fx = load_fixture(SAVES / "retired-account.json", "retired-account on a full phone, erase, then undo")
    fx.fill = True

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        if ph.page.evaluate("() => localStorage.getItem('hikmat_current') === null"):
            probs.append("no fold was waiting, so the check proved nothing")
            return
        why = start_fresh(ph.page)
        if why:
            probs.append(why)
            return
        copies = _undo_copies(ph)
        raws = [r for r in (_local(ph, UNDO), ph.NATIVE.prefs.get(UNDO)) if r]
        if any("Test Learner" in r for r in raws):
            probs.append("the undo copy holds her name")
        if not any(_dict(_dict(u).get("fold")).get("id") == "S-0042" for u in copies.values()):
            probs.append("the undo copy does not say that the erase ended S-0042's fold")
        ph.page.evaluate(DROP_FILLER_JS)
        if _restore(ph) != "restored":
            probs.append("the restore did not finish")
            return
        for which in ("right after", "after a launch", "after another launch"):
            if which != "right after":
                ph.launch("new")
            keys = ph.page.evaluate(STORAGE_JS)["keys"]
            bad = _fold_landed(fx, keys)
            if "hikmat_current" in keys:
                bad.append("hikmat_current is back, so the fold would land a second time")
            if bad:
                probs.append("%s: %s" % (which, _clip(bad, 3)))
                break

    return _rel("U9", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "the undo copy named S-0042's ended fold without her name, and the restore brought its stars back "
                "once, through two more launches")


def second_erase_keeps_first(env, sabotage=None, prelude=None):
    """U10. Start fresh, earn a star in a new lesson, then Start fresh again inside the 7 days. The
    undo copy now runs 7 days from the second erase, and after a relaunch Bring back what I erased
    returns every star from before the first erase as well as the one earned between them."""
    tag = "[%s] second_erase_keeps_first" % env.mode
    fx = _mid()
    (ck, lk), = _fresh_lessons(fx, 1)

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        why = start_fresh(ph.page)
        if why:
            probs.append("the first erase: " + why)
            return
        ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        t0 = time.time() * 1000
        why = start_fresh(ph.page)
        if why:
            probs.append("the second erase: " + why)
            return
        for where, u in sorted(_undo_copies(ph).items()):
            at, until = _num(u.get("erasedAt")) or 0, _num(u.get("until")) or 0
            if until - at != 7 * DAY_MS or abs(at - t0) > 60000:
                probs.append("the undo copy in %s does not run 7 days from the second erase" % where)
        ph.launch("new")
        msg = _restore(ph)
        if msg != "restored":
            probs.append("Bring back what I erased ended with %r" % msg)
            return
        now = _parsed(_local(ph))
        low = lowered(pre, now, GUEST)
        if low:
            probs.append("after a second Start fresh, the restore lost the stars of the first erase: %s" % _clip(low, 3))
        if _learn_star(now, ck, lk) != 3:
            probs.append("the star earned between the two erases, at %s/%s, is gone" % (ck, lk))

    return _rel("U10", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "after a second Start fresh inside the 7 days, the undo copy ran 7 days from it, and the restore "
                "brought back every star from before the first erase and the one earned between them")


# localStorage refuses every write from now on in this launch. A full phone cannot be made to do
# that by filling it: the erased state is smaller than the one it replaces, so it would still fit.
REFUSE_ALL_JS = ("() => { Storage.prototype.setItem = function () { "
                 "throw new DOMException('The quota has been exceeded.', 'QuotaExceededError'); }; }")


def failed_erase_stays_undoable(env, sabotage=None, prelude=None):
    """U11. localStorage refuses every write, as on a phone whose storage is full, so it refuses the
    erase, and the erased state's native write is slow: it lands after the 5 s the erase waits for
    it. She is told the erase failed, so it must not win the next launch with nothing left to undo
    it: her state from before the erase is written after it, and after a relaunch every star is
    still there."""
    tag = "[%s] failed_erase_stays_undoable" % env.mode
    fx = _mid()

    def steps(ph, probs):
        ph.seed(fx)
        ph.launch("new")
        pre = ph.snap()["mem"]
        _settled(ph)
        ph.page.evaluate(REFUSE_ALL_JS)
        ph.NATIVE.slow_next_write = (GUEST, 6500)
        why = start_fresh(ph.page, expect="erasefail")
        if why:
            probs.append("localStorage refused the erase and its native write was slow, so she must be told it "
                         "failed. " + why)
            return
        begin = next((e["i"] for e in reversed(ph.NATIVE.log) if e["op"] == "set:begin" and e.get("key") == GUEST), None)
        if begin is None:
            probs.append("the erase never wrote the native copy, so this proved nothing")
            return
        end = time.monotonic() + 12
        while time.monotonic() < end and not any(e["op"] == "set" and e.get("key") == GUEST and e["i"] > begin
                                                 for e in ph.NATIVE.log):
            ph.page.wait_for_timeout(100)
        ph.page.wait_for_timeout(1500)            # and whatever was queued behind it
        ph.launch("new")
        low = lowered(pre, ph.snap()["mem"], GUEST)
        if low:
            left = "an undo copy is left" if _undo_copies(ph) else "no undo copy is left"
            probs.append("she was told it failed, and after a relaunch her stars are lower, and %s: %s"
                         % (left, _clip(low, 3)))

    return _rel("U11", tag, _full_phone_run(_file_phone(env, sabotage, prelude), steps),
                "an erase told as failed, whose slow native write landed late, left every star after a relaunch")


def _v7_start_fresh(page):
    """v7's Start fresh, as she does it there: My progress, then Start fresh twice (the first tap
    arms it)."""
    page.evaluate("() => openProgressSheet()")
    page.click("#progWipe", timeout=3000)
    page.click("#progWipe", timeout=3000)
    page.wait_for_timeout(500)


def rollback_return_trip(env, sabotage=None, prelude=None):
    """R2. v8 is pulled for v7 and later comes back, on a phone that keeps its data throughout.
    (a) Start fresh in v8; in v7, Start fresh again and two new lessons played. Back on v8 every
        star she earned in v7 is there: the native copy's higher eraseGen must not win.
    (b) No erase in v8; in v7, Start fresh. Back on v8 her erase stands at 0 stars, and Bring back
        what I erased offers, for 7 days, every star the native copy held, as v8's own would.
    (c) v7 loses its WebView data, and she plays one new lesson. Back on v8 the native copy's stars
        come back, with the new one."""
    tag = "[%s] rollback_return_trip" % env.mode
    fx = _mid()
    fresh2 = _fresh_lessons(fx, 2)

    def case_a(ph, pr):
        ph.seed(fx)
        ph.launch("new")
        why = start_fresh(ph.page)
        if why:
            pr.append(why)
            return
        _settled(ph)
        ph.launch("old")
        _v7_start_fresh(ph.page)
        for ck, lk in fresh2:
            ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        ph.launch("old")
        v7 = ph.snap()
        if v7["total"] != 6:
            pr.append("v7 shows %s stars after its Start fresh and two new lessons, not 6, so this proved nothing"
                      % v7["total"])
            return
        ph.launch("new")
        s = ph.snap()
        gone = ["%s/%s" % x for x in fresh2 if _learn_star(s["mem"], *x) != 3]
        if s["total"] < v7["total"] or gone:
            pr.append("back on v8 she has %s stars, not the %s she earned in v7 after its Start fresh%s"
                      % (s["total"], v7["total"], (" (%s gone)" % ", ".join(gone)) if gone else ""))
        _agree(ph, pr, "back on v8,")

    def case_b(ph, pr):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        _settled(ph)
        ph.launch("old")
        _v7_start_fresh(ph.page)
        ph.launch("old")
        if ph.snap()["total"] != 0:
            pr.append("v7's Start fresh left stars, so this proved nothing")
            return
        ph.launch("new")
        s = ph.snap()
        if s["total"] != 0:
            pr.append("back on v8 the %s stars she erased in v7 are back without her asking" % s["total"])
            return
        row = _undo_row(ph)
        if not row or row["days"] != 7:
            pr.append("back on v8 there is no undo row with 7 days left for what she erased in v7 (%r)" % (row,))
            return
        if _restore(ph) != "restored":
            pr.append("Bring back what I erased did not finish")
            return
        low = lowered(pre, _parsed(_local(ph)), GUEST)
        if low:
            pr.append("Bring back what I erased, after v7's Start fresh, left these lower: %s" % _clip(low, 3))

    def case_c(ph, pr):
        ph.seed(fx)
        ph.launch("new")
        pre = _parsed(_local(ph))
        _settled(ph)
        ph.launch("old")
        ph.page.evaluate("() => localStorage.clear()")
        ph.launch("old")
        ck, lk = fresh2[0]
        ph.page.evaluate(STAR_SAVE_JS, [ck, lk])
        ph.launch("old")
        ph.launch("new")
        now = _parsed(_local(ph))
        low = lowered(pre, now, GUEST)
        if low:
            pr.append("after v7 lost its WebView data, back on v8 the native copy's stars are lower: %s" % _clip(low, 3))
        if _learn_star(now, ck, lk) != 3:
            pr.append("the star she earned in v7 after it lost its data, at %s/%s, is gone" % (ck, lk))

    probs = []
    for name, fn in (("(a)", case_a), ("(b)", case_b), ("(c)", case_c)):
        got = _full_phone_run(env.phone(sabotage=sabotage, prelude=prelude), fn)
        probs.extend("%s %s" % (name, p) for p in got)
    return _rel("R2", tag, probs,
                "back on v8 after v7: (a) every star earned in v7 after its Start fresh, over v8's erased native copy; "
                "(b) v7's Start fresh stood, with a 7-day undo of everything; (c) after v7 lost its data, all of it")


for _name, _fn, _modes in (("erase_confirm_layout", erase_confirm_layout, ("web",)),
                           ("erase_guard", erase_guard, ("web",)),
                           ("undo_round_trip", undo_round_trip, ("web", "native")),
                           ("undo_survives_webview_wipe", undo_survives_webview_wipe, ("native",)),
                           ("undo_expires", undo_expires, ("web", "native")),
                           ("restore_beats_stale_native", restore_beats_stale_native, ("native",)),
                           ("restore_keeps_new_play", restore_keeps_new_play, ("web", "native")),
                           ("rollback_after_erase_and_restore", rollback_after_erase_and_restore, ("web", "native")),
                           ("undo_brings_back_waiting_fold", undo_brings_back_waiting_fold, ("native",)),
                           ("second_erase_keeps_first", second_erase_keeps_first, ("web", "native")),
                           ("failed_erase_stays_undoable", failed_erase_stays_undoable, ("native",)),
                           ("rollback_return_trip", rollback_return_trip, ("native",))):
    def _t(env, report, _fn=_fn):
        for _code, ok, msg in _fn(env):
            report.check(ok, msg)
    test(_name, modes=_modes)(_t)


@test("level_test_promotes")
def t_level_test_promotes(env, report):
    for _code, ok, msg in level_test_promotes(env):
        report.check(ok, msg)


@test("caches_move_to_idb")
def t_caches_move_to_idb(env, report):
    for _code, ok, msg in caches_move_to_idb(env):
        report.check(ok, msg)


@test("offline_uses_idb_cache")
def t_offline_uses_idb_cache(env, report):
    for _code, ok, msg in offline_uses_idb_cache(env):
        report.check(ok, msg)


@test("idb_blocked_falls_back")
def t_idb_blocked_falls_back(env, report):
    for _code, ok, msg in idb_blocked_falls_back(env):
        report.check(ok, msg)


@test("storage_full_drops_caches")
def t_storage_full_drops_caches(env, report):
    for _code, ok, msg in storage_full_drops_caches(env):
        report.check(ok, msg)


@test("storage_full_no_caches")
def t_storage_full_no_caches(env, report):
    for _code, ok, msg in storage_full_no_caches(env):
        report.check(ok, msg)


@test("retire_under_full_storage")
def t_retire_under_full_storage(env, report):
    for _code, ok, msg in retire_under_full_storage(env):
        report.check(ok, msg)


@test("storage_full_live_content")
def t_storage_full_live_content(env, report):
    for idb in (True, False):
        for _code, ok, msg in storage_full_live_content(env, idb=idb):
            report.check(ok, msg)


@test("erase_while_fold_pending")
def t_erase_while_fold_pending(env, report):
    for _code, ok, msg in erase_while_fold_pending(env):
        report.check(ok, msg)


@test("retire_frees_queue_room")
def t_retire_frees_queue_room(env, report):
    for _code, ok, msg in retire_frees_queue_room(env):
        report.check(ok, msg)


@test("erase_refused_while_fold_pending", modes=("native",))
def t_erase_refused_while_fold_pending(env, report):
    for _code, ok, msg in erase_refused_while_fold_pending(env):
        report.check(ok, msg)


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
# v7's save, with no guard: on a full phone its setItem throws, and the throw is all the save does.
V7_SAVE = _sab("the save is unguarded, as in v7",
               "save=function(){dropLevelCache();localStorage.setItem(stateKey(),JSON.stringify(state));};")
# A write that makes room by throwing away whatever is not a saved state: filler, the settings
# cache, any key this build does not know. It keeps the star and loses someone else's data.
GREEDY_WRITE = _sab("a write makes room by dropping keys that are not its to drop",
                    "var real=writeLocal;writeLocal=function(k,j){if(real(k,j))return true;"
                    "for(var i=localStorage.length-1;i>=0;i--){var x=localStorage.key(i);"
                    "if(x&&x.indexOf('hikmat_state_')!==0)localStorage.removeItem(x);}"
                    "try{localStorage.setItem(k,j);return true;}catch(e){return false;}};")
# The lessons cache written with a bare setItem again, as loadFromBackend did before the guard:
# on a full phone the throw lands inside its try and skips everything after it.
BARE_CACHE_WRITE = _sab("the lessons cache is written with a bare setItem again",
                        "var real=writeLocal;writeLocal=function(k,j){if(k==='hikmat_courses_cache'){"
                        "localStorage.setItem(k,j);return true;}return real.apply(this,arguments);};")
# setItem never throws, so every refused write looks as if it landed. That is v7's fold, which
# swallowed the error and dropped hikmat_current as though the account were safely folded in.
SILENT_SETITEM = ("/* e2e self-test: a refused write looks like one that landed */(function(){var s=Storage.prototype.setItem;"
                  "Storage.prototype.setItem=function(){try{return s.apply(this,arguments);}catch(e){}};})();")
# GREEDY_WRITE one level down, at Storage itself, and before the game's scripts: a refused write
# deletes every key that is not a saved state and tries again. Only this reaches the account fold,
# which retireAccounts() writes before any script appended at the end of the page has run.
GREEDY_STORAGE = ("/* e2e self-test: a refused write makes room by deleting other keys */(function(){"
                  "var s=Storage.prototype.setItem,r=Storage.prototype.removeItem;"
                  "Storage.prototype.setItem=function(k){try{return s.apply(this,arguments);}catch(e){"
                  "for(var i=this.length-1;i>=0;i--){var x=this.key(i);if(x&&x.indexOf('hikmat_state_')!==0&&x!==k)r.call(this,x);}"
                  "return s.apply(this,arguments);}};})();")
# Start fresh as it was before it ended a waiting account fold: hikmat_current stays, and the
# first launch with room lands the fold in the profile she has just erased. retireAccounts() keeps
# the real endAccountFold(), because it runs before this script does.
KEEP_WAITING_FOLD = _sab("Start fresh leaves a waiting account fold in place", "endAccountFold=function(){};")
# retireAccounts() in its old order: the unsendable queues and the social marker go only after the
# fold has been tried, so the fold cannot use the room they held.
LATE_QUEUE_DROP = ("/* e2e self-test: the queues go after the fold */(function(){var r=Storage.prototype.removeItem;"
                   "Storage.prototype.removeItem=function(k){var me=this;"
                   "if(/_queue$|^hikmat_social_pending$/.test(String(k))){setTimeout(function(){r.call(me,k);},0);return;}"
                   "return r.apply(this,arguments);};})();")
# The move copies the caches into IndexedDB but never takes them out of localStorage, so they
# still crowd her progress there. Storage-level, and before the game's scripts, so it reaches the
# move however early it runs.
KEEP_CACHES = ("/* e2e self-test: the caches never leave localStorage */(function(){var r=Storage.prototype.removeItem;"
               "Storage.prototype.removeItem=function(k){if(/^hikmat_(courses|structure|testbank)_cache$/.test(String(k)))return;"
               "return r.apply(this,arguments);};})();")
# The content store is written but never read back: every read finds nothing, so offline the game
# has only its small bundled fallback, as if the caches had been thrown away.
UNREAD_STORE = _sab("the content store is never read back", "contentStore.get=function(){return Promise.resolve(null);};")
# The move as it must never be: each cache leaves localStorage when its write to IndexedDB starts,
# not when IndexedDB confirms it, and the write then fails (here it is aborted, as when the app is
# closed mid-write). The cache is in neither place, and offline she has only the bundled fallback.
REMOVE_BEFORE_COMMIT = ("/* e2e self-test: a cache leaves localStorage before IndexedDB confirms it */(function(){"
                        "var put=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(v,k){"
                        "try{localStorage.removeItem(k);}catch(e){}var r=put.apply(this,arguments);"
                        "try{this.transaction.abort();}catch(e){}return r;};})();")
# Every lesson from level 2 up is shut until she reaches its level: the ladder wall that "nothing
# locks" replaced.
LEVEL_WALL = _sab("lessons above her level are locked again", "var orig=lessonLock;lessonLock=function(course,ls){"
                  "if(course&&ls&&lessonLevel(course,ls)>studentLevel())return{kind:'level',level:lessonLevel(course,ls)};"
                  "return orig.apply(this,arguments);};")
# Every save leaves the level-test results out, so a pass she has just earned is never stored and
# the next launch has her back at level 1.
UNSAVED_PASS = _sab("a level-test pass is never stored", "var real=save;save=function(){var t=state.levelTests;"
                    "state.levelTests={};try{return real.apply(this,arguments);}finally{state.levelTests=t;}};")
# The mirror gate opens as soon as the page has run, before the launch has read the native copy,
# so a boot save can overwrite the only copy of her progress before it is read.
EARLY_GATE = _sab("the mirror opens before the native copy is read", "_mirrorOpen=true;_mirrorPump();")
# The launch merge ignores eraseGen: an erased copy and an older one just merge to the higher
# values, so a stale copy, or a fold that lands after the erase, brings every erased star back.
ERASEGEN_IGNORED = _sab("the launch merge ignores eraseGen", "mergeCopy=function(l,o,m){if(!o)return l;"
                        "var c=JSON.parse(JSON.stringify(m?o:l));return mergeSavedState(c,m?l:o,{sameLearner:true});};")
# The native copy simply replaces hers: an older native copy lowers her stars and gems.
NATIVE_REPLACES = _sab("the native copy replaces hers", "mergeCopy=function(l,o){return o?JSON.parse(JSON.stringify(o)):l;};")
# dropContentCaches() takes the settings cache too, which is not one of the three it may drop.
DROP_SETTINGS_TOO = _sab("making room also drops the settings cache",
                         "var real=dropContentCaches;dropContentCaches=function(){real();"
                         "localStorage.removeItem('hikmat_settings_cache');};")
# Start fresh ends a waiting fold without carrying the guide and Hindi form it holds, as 26e13b3
# did: hikmat_current goes, and with it the only copy of both. retireAccounts() keeps the real
# carry, because it runs before this script does.
NO_CARRY = _sab("Start fresh ends a waiting fold without what it carries", "carryMascotAndGender=function(into){return into;};")
# Start fresh removes hikmat_current whether or not its save lands, as 26e13b3 did. A refused
# erase then loses the only copy of the guide and Hindi form, and the fold with it.
FOLD_GOES_FIRST = _sab("Start fresh removes hikmat_current whether or not its save lands",
                       "var real=eraseWaitingFold;eraseWaitingFold=function(st){real(st);endFoldWhenKept=false;endAccountFold();};")
# The fold ends only if Start fresh's own save lands. When that one is refused and a later save
# lands, the erased profile is stored but hikmat_current stays, and the next launch with room
# folds the account's stars into the profile she erased.
FOLD_ENDS_WITH_ERASE_ONLY = _sab("only Start fresh's own save can end a waiting fold",
                                 "var real=save;save=function(){var k=real.apply(this,arguments);endFoldWhenKept=false;return k;};")


# Task 5. Bringing a file replaces her state with the file's, so an older file lowers her.
BRING_REPLACES = _sab("a brought file replaces her state", "mergeBrought=function(cur,clean){return JSON.parse(JSON.stringify(clean));};")
# Bringing a file merges at once, without the confirm sheet.
BRING_NO_ASK = _sab("a file is brought in without asking", "askBring=function(){return Promise.resolve(true);};")
# The file's eraseGen is taken along with it.
BRING_TAKES_ERASEGEN = _sab("a brought file's eraseGen is taken", "var c0=cleanImportedState;cleanImportedState=function(o){"
                            "var r=c0(o);if(o&&typeof o.eraseGen==='number')r.eraseGen=o.eraseGen;return r;};var m0=mergeBrought;"
                            "mergeBrought=function(cur,clean){var n=m0(cur,clean);if('eraseGen' in clean)n.eraseGen=clean.eraseGen;return n;};")
# The file's settings are taken even when she has progress (D5 ignored).
BRING_TAKES_SETTINGS = _sab("a brought file's settings always win", "var m1=mergeBrought;mergeBrought=function(cur,clean){"
                            "var n=m1(cur,clean);SETTING_FIELDS.forEach(function(f){if(clean[f]!==undefined)n[f]=clean[f];});return n;};")
# A file is trusted as it comes.
BRING_UNCLEANED = _sab("a brought file is not cleaned", "cleanImportedState=function(o){return o;};")
# The file carries every profile on the phone, the retired account's too.
FILE_HOLDS_OTHERS = _sab("the file holds every profile on the phone", "var p0=progressFileText;progressFileText=function(st){"
                         "var o=JSON.parse(p0(st));o.others={};for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i);"
                         "if(k.indexOf('hikmat_state_')===0)o.others[k]=1;}return JSON.stringify(o);};")


# Task 6. Erase takes a tap the moment its sheet opens.
NO_ERASE_GUARD = _sab("Erase has no 600 ms guard", "ERASE_GUARD_MS=0;")
# Erase sits last, under her thumb, and Keep it above it.
ERASE_UNDER_THUMB = _sab("Erase and Keep it swap places", "var o6=openEraseSheet;openEraseSheet=function(){var r=o6.apply(this,arguments);"
                       "var y=document.getElementById('eraseYes'),k=document.getElementById('eraseKeep');"
                       "y.parentNode.insertBefore(k,y);return r;};")
# The restore replaces her state with the undo copy, so what she earned since the erase goes.
RESTORE_REPLACES = _sab("the restore replaces her state with the undo copy", "mergeRestore=function(cur,u){"
                        "var r=JSON.parse(JSON.stringify(u.state));r.eraseGen=(cur.eraseGen||0)+1;return r;};")
# The restore keeps the undo copy's own eraseGen, below the erased copy's.
RESTORE_OLD_ERASEGEN = _sab("the restore keeps the undo copy's eraseGen", "var m6=mergeRestore;mergeRestore=function(c,u){"
                            "var r=m6(c,u);r.eraseGen=u.state.eraseGen||0;return r;};")
# An expired undo copy is never removed.
# A prelude, not a script after the game's: on the web the launch's sweep runs as soon as the
# game's own script ends, before anything appended after it.
UNDO_NEVER_SWEPT = ("/* e2e self-test: an undo copy is never removed */(function(){var r=Storage.prototype.removeItem;"
                    "Storage.prototype.removeItem=function(k){if(String(k).indexOf('hikmat_undo_')===0)return;"
                    "return r.apply(this,arguments);};})();")
# The undo copy is kept in localStorage only.
UNDO_LOCAL_ONLY = _sab("the undo copy is never written natively", "_nativePut=function(){return Promise.resolve(false);};")
# The undo copy forgets the account fold the erase ended.
UNDO_FORGETS_FOLD = _sab("the undo copy forgets the fold the erase ended", "var u6=undoCopyOf;undoCopyOf=function(st){"
                         "var u=u6(st);delete u.fold;return u;};")
# A second Start fresh writes its undo copy over the first one's.
UNDO_OVERWRITES = _sab("a second erase overwrites the first one's undo copy", "withEarlierUndo=function(u){return u;};")
# A failed erase drops its undo copy at once, and never writes her state back after the erased one.
ERASE_FAIL_DROPS_UNDO = _sab("a failed erase drops its undo copy at once",
                             "eraseNotKept=async function(b,f){state=b;endFoldWhenKept=f;await dropUndo();};")
# A launch that never merged the native copy leaves no mark. A prelude: the mark is written while
# the game's own script runs, before anything appended after it.
NO_UNMERGED_MARK = ("/* e2e self-test: no unmerged mark */(function(){var s=Storage.prototype.setItem;"
                    "Storage.prototype.setItem=function(k){if(String(k)==='hikmat_native_unmerged')return;"
                    "return s.apply(this,arguments);};})();")
# The launch merge does not see an erase made by v7.
OLDER_ERASE_IGNORED = _sab("the launch merge is blind to an erase made by v7",
                           "adoptOlderErase=function(){return Promise.resolve(false);};")
# A file's keys may name what every object inherits again.
INHERITED_KEYS = _sab("a file's keys may name inherited members",
                      "_safeKeys=function(o){return _isObj(o)?Object.keys(o).filter(function(k){"
                      "return k.length>0&&k.length<=140&&!UNSAFE_KEYS.has(k);}):[];};")


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
    as the fixtures, or onto the same full phone as S1-S7, and each must fail exactly the check it
    breaks, naming what it broke. A build that is slow but correct must still pass, and a private
    save's output must carry nothing of hers."""
    tag = "[%s] self-test" % env.mode
    leaks = _private_output_problems()
    report.check(not leaks, "%s a private save's failures show no value, count, old student id or page text%s"
                 % (tag, (": " + _clip(leaks, 3)) if leaks else ""))

    mid = load_fixture(SAVES / "v7-midway.json", "v7-midway (sabotaged)")
    ck, lk, aid = _played(mid)

    def judged(what, res, codes, named):
        failed = {code: msg for code, ok, msg in res if not ok}
        for msg in failed.values():
            print("    caught: " + msg)
        ok = set(failed) == set(codes) and all(text in failed.get(code, "") for code, text in named)
        # "and nothing else" only when other checks ran on the same phone. A full-phone test is a
        # single check, and saying it of one would claim something that was never looked at.
        alone = " and nothing else" if len(res) > len(codes) else ""
        said = ("fails %s%s, naming %s" % (" and ".join(codes), alone, ", ".join(t for _c, t in named))
                if codes else "passes every check, because nothing was lost")
        report.check(ok, "%s %s %s%s" % (tag, what, said, "" if ok else " (failed: %s)" % (", ".join(sorted(failed)) or "nothing")))

    def expect(what, fx, codes, named, script=None, prelude=None, direct=False):
        judged(what, update_results(env, fx, sabotage=script, prelude=prelude, direct=direct), codes, named)

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
    # A full phone with nothing to drop refuses her next save, so a number boot changed only in
    # memory never reaches storage, and I5 must say that its comparison proved nothing.
    full = load_fixture(SAVES / "full-storage.json", "full-storage without caches (sabotaged)")
    full.with_caches = False
    expect("gems added in memory only, on a full phone that refuses her next save", full, ["I5"],
           [("I5", "her next save was refused")], script=_sab("gems grow in memory only", "state.coins+=100000;"))
    expect("content that arrives 700 ms late", mid, [], [], prelude=SLOW_FETCH)
    judged("a save that lets a full phone's error escape, as v7's did", storage_full_drops_caches(env, sabotage=V7_SAVE),
           ["S1"], [("S1", "QuotaExceededError")])
    judged("a write that makes room by dropping the settings cache too", storage_full_drops_caches(env, sabotage=DROP_SETTINGS_TOO),
           ["S1"], [("S1", "removed to make room: hikmat_settings_cache")])
    judged("a write that makes room by dropping keys that are not caches", storage_full_no_caches(env, sabotage=GREEDY_WRITE),
           ["S2"], [("S2", "removed to make room")])
    judged("a refused write that looks as if it landed", retire_under_full_storage(env, prelude=SILENT_SETITEM),
           ["S3"], [("S3", "hikmat_current")])
    judged("an account fold that makes room by deleting other keys", retire_under_full_storage(env, prelude=GREEDY_STORAGE),
           ["S3"], [("S3", "removed to make room")])
    # Since Task 3 the lessons cache is written by contentStore.set(), outside loadFromBackend()'s
    # try, so a bare setItem that throws can no longer skip the settings or clear backendLive. It
    # surfaces as an unhandled rejection instead, a page error naming the quota.
    judged("a lessons cache written with a bare setItem again", storage_full_live_content(env, sabotage=BARE_CACHE_WRITE, idb=False),
           ["S4b"], [("S4b", "QuotaExceededError")])
    judged("caches copied into IndexedDB but never taken out of localStorage", caches_move_to_idb(env, prelude=KEEP_CACHES),
           ["C1"], [("C1", "still in localStorage")])
    judged("a content store that is never read back", offline_uses_idb_cache(env, sabotage=UNREAD_STORE),
           ["C2"], [("C2", "never loaded")])
    judged("a cache taken out of localStorage before IndexedDB confirms it", offline_uses_idb_cache(env, prelude=REMOVE_BEFORE_COMMIT),
           ["C2"], [("C2", "never loaded")])
    judged("lessons above her level locked again", level_test_promotes(env, sabotage=LEVEL_WALL),
           ["LT2"], [("LT2", "lessons are locked")])
    judged("a level-test pass that is never stored", level_test_promotes(env, sabotage=UNSAVED_PASS),
           ["LT1"], [("LT1", "after a relaunch she is at level 1")])
    # S5 runs its full case in the app; on the web there is no room for the undo copy, and the
    # erase is refused (Task 6).
    nenv = env if env.mode == "native" else Env(env.browser, env.base, "native", env.old_ref, env.old_html, env.new_html)
    judged("Start fresh that leaves a waiting account fold in place", erase_while_fold_pending(nenv, sabotage=KEEP_WAITING_FOLD),
           ["S5"], [("S5", "waiting fold landed")])
    carried = _carried(load_fixture(SAVES / "retired-account.json", "retired-account"))
    judged("Start fresh that ends a waiting fold without its guide and Hindi form", erase_while_fold_pending(nenv, sabotage=NO_CARRY),
           ["S5"], [("S5", "not %s and %s, which only hikmat_current held" % (carried["gender"], carried["mascot"]))])
    judged("an account fold tried before the unsendable queues are gone", retire_frees_queue_room(env, prelude=LATE_QUEUE_DROP),
           ["S6"], [("S6", "hikmat_current is still there")])
    # S7 and the native copy's checks need a phone with the app's native side. The self-test runs
    # once, in the first mode, so it brings its own.
    judged("Start fresh that removes hikmat_current whether or not its save lands",
           erase_refused_while_fold_pending(nenv, sabotage=FOLD_GOES_FIRST),
           ["S7"], [("S7", "hikmat_current went although localStorage refused the erase")])
    judged("a waiting fold that only Start fresh's own save can end",
           erase_refused_while_fold_pending(nenv, sabotage=FOLD_ENDS_WITH_ERASE_ONLY),
           ["S7"], [("S7", "hikmat_current is still there after her next save")])
    judged("a mirror that opens before the native copy is read", boot_save_on_empty_webview(nenv, sabotage=EARLY_GATE),
           ["N9"], [("N9", "before the launch had read it")])
    judged("a launch merge that ignores eraseGen", erase_with_waiting_fold_and_stale_mirror(nenv, sabotage=ERASEGEN_IGNORED),
           ["N6b"], [("N6b", "stars and")])
    judged("Start fresh that leaves a waiting fold in place, with a stale native copy",
           erase_with_waiting_fold_and_stale_mirror(nenv, sabotage=KEEP_WAITING_FOLD), ["N6b"], [("N6b", "(a)")])
    judged("a native copy that replaces hers", native_lower_never_wins(nenv, sabotage=NATIVE_REPLACES),
           ["N4"], [("N4", "coins")])
    judged("a brought file that replaces her state", bring_never_lowers(env, sabotage=BRING_REPLACES),
           ["F3"], [("F3", "coins")])
    judged("a file brought in without asking", bring_asks_and_follows_d5(env, sabotage=BRING_NO_ASK),
           ["F1c"], [("F1c", "without asking")])
    judged("a brought file whose eraseGen is taken", bring_asks_and_follows_d5(env, sabotage=BRING_TAKES_ERASEGEN),
           ["F1d"], [("F1d", "eraseGen")])
    judged("a brought file whose settings always win", bring_asks_and_follows_d5(env, sabotage=BRING_TAKES_SETTINGS),
           ["F1d"], [("F1d", "her settings became")])
    judged("a brought file that is not cleaned", bring_rejects_bad_files(env, sabotage=BRING_UNCLEANED),
           ["F4"], [("F4", "polluted")])
    judged("a file that holds every profile on the phone", save_bring_round_trip(env, sabotage=FILE_HOLDS_OTHERS),
           ["F1b"], [("F1b", "S-0042")])
    judged("an Erase with no 600 ms guard", erase_guard(env, sabotage=NO_ERASE_GUARD),
           ["U2"], [("U2", "as the sheet opened")])
    judged("an Erase under her thumb", erase_confirm_layout(env, sabotage=ERASE_UNDER_THUMB),
           ["U1"], [("U1", "Erase lies within 16 px")])
    judged("a restore that replaces her state", restore_keeps_new_play(env, sabotage=RESTORE_REPLACES),
           ["U7"], [("U7", "earned after the erase")])
    judged("a restore that keeps the undo copy's eraseGen", restore_beats_stale_native(nenv, sabotage=RESTORE_OLD_ERASEGEN),
           ["U6"], [("U6", "stale native copy")])
    judged("an undo copy that is never removed", undo_expires(env, prelude=UNDO_NEVER_SWEPT),
           ["U5"], [("U5", "still in")])
    judged("an undo copy kept in localStorage only", undo_survives_webview_wipe(nenv, sabotage=UNDO_LOCAL_ONLY),
           ["U4"], [("U4", "no undo row")])
    judged("an undo copy that forgets the ended fold", undo_brings_back_waiting_fold(nenv, sabotage=UNDO_FORGETS_FOLD),
           ["U9"], [("U9", "S-0042")])
    judged("a second Start fresh that overwrites the first one's undo copy",
           second_erase_keeps_first(env, sabotage=UNDO_OVERWRITES), ["U10"], [("U10", "first erase")])
    judged("a failed erase that drops its undo copy while the erased state is still on its way",
           failed_erase_stays_undoable(nenv, sabotage=ERASE_FAIL_DROPS_UNDO), ["U11"], [("U11", "told it failed")])
    judged("a launch that never merged the native copy and leaves no mark",
           settings_survive_unread_native(nenv, prelude=NO_UNMERGED_MARK), ["N10"], [("N10", "settings")])
    judged("a launch merge blind to an erase made by v7", rollback_return_trip(nenv, sabotage=OLDER_ERASE_IGNORED),
           ["R2"], [("R2", "v7")])
    judged("a file whose keys name inherited members", bring_ignores_inherited_names(env, sabotage=INHERITED_KEYS),
           ["F8"], [("F8", "toString")])


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
