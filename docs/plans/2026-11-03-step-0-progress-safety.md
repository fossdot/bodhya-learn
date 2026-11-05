# Step 0: progress safety release (v8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a release, with no visual change apart from the progress and erase sheets, that closes every loss gap the spec lists for Step 0:
- the unguarded save
- caches sharing her space
- no second copy
- no way to carry progress
- the double tap on Start fresh

Tests that replay real saves through the update must prove that no star total drops and no played lesson closes.

**Architecture:** All game code stays in the single file `Hikmat Games/index.html`, synced by `sync-game.sh`.
- Progress stays in localStorage, and the content caches move to IndexedDB.
- Every save is also mirrored to Capacitor Preferences, but only after the launch has read the native copy. That copy is merged back at launch, keeping the higher value.
- A JSON progress file goes out through the share sheet and comes back through a file picker.
- Start fresh gets its own confirm sheet and keeps a 7-day undo copy.
- A Python Playwright harness serves `hikmat/public/` statically, seeds saved states, loads the old build (git ref `play-v7`) and then the new one on the same origin, and compares the two.

**Tech Stack:** Vanilla JS/HTML. Capacitor 8 with `@capacitor/preferences@8.0.1`, `@capacitor/share@8.0.3` and `@capacitor/filesystem@8.1.5`; none of them adds an Android permission (checked in their manifests). Preferences writes `shared_prefs/CapacitorStorage.xml` with `apply()`. Share uses `<package>.fileprovider`, which the manifest and `file_paths.xml` (cache-path) already declare. Python Playwright, Chromium, IndexedDB.

**Spec:** `docs/progress-logic.md` (rules 1–12, "Where data can be lost today"). Brief: the redesign brief of 3 Nov 2026, "Step 0". Layout of the erase sheet: Figma file `nZSfrHxWNOojo07J8Z7ZG4`, page 06, frame "Erase confirm" (node 23:244). Figma wins on layout, and the doc wins on logic.

**Status, 3 Nov:**
- I ran the key-check script against `Hikmat Games/` (which builds v7) and against `apps/hikmat`.
- Every "Must exist" key was found and every "Must be free" key is free.
- Identity: `appId` and `applicationId` are `net.bodhya.learn`, `androidScheme` is `https`, and there is no `hostname`, so Capacitor's default `localhost` applies.
- Vishal reviewed the first plan and added: the boot race, rollback playedOpen, erase sheet with 7-day undo, Hindi copy, and closed testing first.
- D1–D6 are accepted.

**Status, 4 Nov:** Q1 and Q2 are answered yes (see Open questions), so Task 0 is no longer blocked. Vishal added N6b (erase with a waiting fold and a stale mirror at once) and N9 (boot save on an empty WebView) to Task 4. The five additions from the first review are all in: the boot race (BOOT_STATE, the mirror gate, N8, N9), playedOpen in R, the erase sheet with 7-day undo (Task 6), neutral Hindi (the save-file line uses "रखा जा सकता है"), and closed testing first (Task 7). बोध्या was checked on 4 Nov: it is the only Devanagari spelling in the game, the Play listing docs and the feature graphic. The wordmark is Latin only.

## Global Constraints

- "Students' local progress must never be lost."
- "Never delete, rename or reshape a stored key or field. Never change the package name, the signing key, or the Capacitor scheme and hostname." New fields and keys are added. The only removals are the three rebuildable cache keys (D1) and the 7-day undo copy, which removes itself when it expires or is used.
- "Never rename course, lesson or game type keys."
- Rule 2: "Any merge, import or restore keeps the higher value." The only things that may lower a number are her own Erase and an expired undo.
- "Every string exists in English and Hindi. Check Hindi layouts at 360 x 640." Write every string with `tx(en, hi)`.
  - **Hindi never addresses her in the masculine.** Prefer neutral forms (imperatives such as सहेजो, passives such as "रखा जा सकता है"), or use the app's `{fem|masc}` marker (`गई{ी|ा}`, resolved by `gform()`).
  - The Devanagari brand name is बोध्या. That is how the app, the Play listing and the feature graphic write it. There is no Devanagari wordmark to check it against: the in-app wordmark is Latin BODHYA, drawn as paths.
- "Tap targets are at least 48dp. Text contrast meets WCAG AA in both themes."
- **Non-readers navigate by sound.** Each new sheet says its title through `srSay`, and the 🔊 button reads the sheet: title, body, and what each button does.
- Offline only: no CDN, no external fonts or assets. Escape dynamic strings with `esc()` / `escAttr()`.
- Edit only `/Users/fossdot/code/Hikmat Games/index.html`, then run `/Users/fossdot/Developer/hikmat-bench/apps/hikmat/sync-game.sh`. That copies it to `apps/hikmat/index.html` and `apps/hikmat/hikmat/public/game.html` and re-bundles `android-app/www`. Tests run against `hikmat/public/game.html`. Bump `CACHE` in `hikmat/public/sw.js` once per PR whenever game.html changes.
- The Play AAB has INTERNET as its only permission and the upload key `2A:6C:…:DC:26`.
- Release to Android only, closed testing first (Task 7). Don't deploy nightly to the web: `AUTH_ENABLED=false` on the web would fold only the signed-in girl on a shared laptop into the device profile, and step 5 fixes that.
- **Git:** work on branch `safety/step-0`. It stacks on `fix/topbar-long-back-label`, which holds D6.
  - Commit after each task. Commit messages are plain sentences in this repo's style, such as "A full phone no longer throws away the stars she just earned", with a body saying why. Never add `Co-Authored-By` or any AI attribution.
  - Never push; the lead does that.
  - `android-app/` is not in git. Edit it in place, and the lead records the diffs in the PR.

## Decisions

- **D1.** Each of the three cache keys is removed from localStorage once IndexedDB holds it. v7 still works without them: it falls back to the bundled `curriculum.json`, and Test R proves this.
- **D2.** New state field **`eraseGen`**, an integer that defaults to 0. Start fresh adds 1 to it, and restoring an erase sets it to one more than the higher of the two copies' values. A copy with a lower `eraseGen` is never merged into one with a higher value.
- **D3.** When two copies of the same learner merge, `levelTests[n].attempts` takes the max. Without opts, `mergeSavedState` behaves exactly as today, for `retireAccounts`.
- **D4.** Real tester saves go in `apps/hikmat/tests/saves/private/`, which is gitignored. The harness runs them when they are present. Synthetic fixtures are committed.
- **D5.** Bringing a file adopts its settings only when this profile has no progress yet. Otherwise only progress merges.
- **D6.** The top-bar fix for long back labels gets its own PR, and ships in v8.
- **D7.** The undo copy lives under the new key **`hikmat_undo_<id>`** (`hikmat_undo_guest` today), in localStorage and in Preferences. It is `{kind:"bodhya-erase-undo", v:1, erasedAt:ms, until:ms, state:{…}}`. It is never merged automatically, only restored on purpose.
- **D8.** In the My progress sheet, Start fresh moves to the very end, below Close and set apart, following Figma's "Erase sits last, apart from the rest". Sheets are always bottom-anchored (`.sheetwrap { align-items:flex-end }`). So the confirm sheet's "Keep it" button, the last one, sits where her thumb was, and Erase sits above it.

## Review Focus

1. **A save during boot must not overwrite the native copy before it is read.**
   - The WebView is empty, native holds her progress, and `retireAccounts` or a boot `save()` writes first.
   - The native copy must survive and win. Covered by Task 4, N8.
2. **Relaunching must not inflate anything.** Three launches in a row give an identical state: attempts, gems and seen-lists stay the same. Covered by Task 4, N5.
3. **An erase must stay erased, and its undo must really bring everything back.**
   - Neither the mirror nor a lost native write may resurrect erased stars.
   - Undo restores every star, while play since the erase is kept.
   - Covered by Task 4 (N6) and Task 6 (U3–U7).
4. **A file that isn't ours is refused without touching her progress.** That covers a photo, cut-off JSON, another app's JSON, `__proto__` keys, star values like 99 or "3", and a 3 MB file. Covered by Task 5, F4.
5. **Pulling v8 must be safe.** v7 reads what v8 left, with the same totals, every played lesson open, and the full curriculum. Covered by Task 1 (R), Task 3 (C4) and Task 6 (U8).

---

### Task 0: put `android-app/` under git (Q1: yes)

- Copy `android-app/` into `apps/hikmat/android-app/` without signing material, generated output or `node_modules`. Point `sync-game.sh` and `build.sh` at the copy in the repo.
- The keystores stay outside the repo. Run a secret scan and show Vishal the staged file list before committing.
- Pin `"hostname": "localhost"` in `capacitor.config.json` (Q2), in its own commit.

### Task 1: Harness, fixtures and update invariants

**Files:**
- Create: `apps/hikmat/e2e-progress-safety.py`, `apps/hikmat/tests/saves/README.md` (format, how to add a tester's file, consent) and `apps/hikmat/tests/saves/*.json` (generated)
- Modify: `apps/hikmat/.gitignore` (+ `tests/saves/private/`)
- Copy: the spec to `apps/hikmat/docs/progress-logic.md` and this plan to `apps/hikmat/docs/plans/`
- The lead has already created tag `play-v7` (97265c2) and checked it against the v7 AAB.

**Interfaces (produces):**
- **Run:** `/tmp/pw/bin/python e2e-progress-safety.py [--old play-v7] [--mode web|native|both] [--only <name>] [--self-test] [--make-fixtures]`. It prints `PASS`/`FAIL` lines in the style of `e2e-levels.py`, and exits 0 only when all pass. Target: under 5 min for `--mode both`.
- **Fixture formats:**
  - **Export envelope** `{"kind":"bodhya-progress","v":1,"app":"net.bodhya.learn","savedAt":ISO,"state":{…}}`. It seeds `hikmat_state_guest`.
  - **Storage snapshot** `{"kind":"storage-snapshot","note":"…","localStorage":{key: rawString}, "withCaches"?:true, "fillToQuota"?:true}`. `withCaches` adds the three cache keys built from `hikmat/public/curriculum.json` and `testbank.json`, at real size. `fillToQuota` adds `zz_filler_*` keys until `setItem` throws.
- **Harness:**
  - Thread HTTP server over `hikmat/public/` on a free port.
  - `/game-old.html` is fulfilled via `page.route` from `git show <old>:hikmat/public/game.html`. `/game.html` is the new build.
  - Contexts use `service_workers="block"` and viewport 360×640. Collect `pageerror` and console errors, and fail on any.
  - Every request to a host other than the local server is aborted, so a test can never reach learn.bodhya.net.
  - The plugin API shapes come from the real `definitions.d.ts` of `@capacitor/preferences@8.0.1`, `@capacitor/filesystem@8.1.5` and `@capacitor/share@8.0.3` (`npm pack` each one; the file is `package/dist/esm/definitions.d.ts`, and the Android sources sit beside it).
- **Native mode:** an init script defines `window.Capacitor = {Plugins:{Preferences, Filesystem, Share}}` before any page script. The calls go through `page.expose_function` to a Python `NATIVE` dict that survives reloads and `localStorage.clear()`.
  - Preferences: `get({key})→{value}`, `set({key,value})`, `remove({key})`, `keys()→{keys}`.
  - Filesystem: `writeFile({path,data,directory,encoding})→{uri:"file:///cache/"+path}`, recording the data.
  - Share: `share({title,files})`, recording the call.
  - Switches: `NATIVE.drop_next_write` (the next `set` resolves but stores nothing) and `NATIVE.get_delay_ms`. A log records every `set`, with the order of each `get` and `set`.
- **`SNAP` (in-page JS):** `{stateKeys:{k:parsed}, total: totalStars(), level: studentLevel(), playedOpen:[[ck,lk]…], settings:{lang,theme,sound,gender,mascot,prefsTouched}, coursesLen: COURSES.length}`. `playedOpen` lists lessons that have a star, exist in `COURSES`, and have `lessonLock(course, ls) === null`.
- **Boot wait:** `COURSES.length > 5`, then `await` `contentReady` and `nativeRestore` when they are defined. The `typeof` guard means it works for v7.

- [ ] **Step 1: Generate fixtures.** `--make-fixtures` loads `play-v7` and builds states with the game's own `levelsFor()`, so game ids are real. The fixtures:
  - `v7-midway` (envelope): about 40 lessons in 6 courses; coins 1240; `levelTests {"1":{passed:true,bestPct:85,attempts:2}}`; `testSeen` L1/L2; `seenTypes`; `videosSeen`; `lastLoc`/`lastMode`; settings lang hi, theme dark, gender Female, mascot set.
  - `pre-ledger` (snapshot): no `seenTypes`, `levelTests`, `testSeen` or `videosSeen`.
  - `retired-account` (snapshot): `hikmat_current {id:"S-0042", gender:"Male", mascot:<a different one from v7-midway>}`, `hikmat_state_S-0042` with 15 lessons (some not in v7-midway), and a small guest state.
  - `two-accounts` (snapshot): `hikmat_state_A`, `hikmat_state_B`, and `hikmat_current` pointing at A.
  - `full-storage` (snapshot): v7-midway, `withCaches`, `fillToQuota`.
  - `heavy` (envelope): every game of every lesson at 3 stars, coins 99999, all five `levelTests` passed.
- [ ] **Step 2: Write the update checks.** For each fixture, seed it, load old and take `SNAP` as A, then load new and take `SNAP` as B, then relaunch new three times and take B3. Fail, naming the fixture and the key, on any of these:
  - **I1:** a seeded `hikmat_state_*` key is missing in B, or any star, `coins`, `bestPct` or `attempts` is lower than in the seed, or any `passed` turns false.
  - **I2:** `B.total < A.total` or `B.level < A.level`.
  - **I3:** `A.playedOpen ⊄ B.playedOpen`.
  - **I4:** `B.settings ≠ A.settings`.
  - **I5:** `B3 ≠ B`.
  - **R (rollback):** after B, load old again and take `SNAP` as R. Fail if `R.total ≠ B.total`, `R.level ≠ B.level`, `B.playedOpen ⊄ R.playedOpen`, or `R.coursesLen` is not the length of `curriculum.json`.
- [ ] **Step 3: Add `--self-test`.** It routes the new build with an appended script that sets one played star to 0 and calls `save()`. The harness must report FAIL for I1 and I2, and self-test passes only if it does. A second sabotage changes `lessonLock` to lock one played lesson, and I3 must fail.
- [ ] **Step 4: Run** `--old play-v7 --mode both` against the current game.html. Expected: all PASS, and the self-test catches both sabotages. If something fails here, it is a bug in v7, so report it before going on.
- [ ] **Step 5: Commit:** "Saves go through every update under test: v7 to the new build and back".

### Task 2: Guarded save (rule 4)

**Files:** in `index.html`: `save` (~1855), the unguarded `localStorage.setItem(CACHE_KEY…)` in `loadFromBackend` (~6674), and `retireAccounts` (~1909).

**Interfaces (produces):**
- `writeLocal(key: string, json: string) → boolean`. Any throw from `setItem` counts as full: call `dropContentCaches()` and retry once. Never remove progress to make room.
- `dropContentCaches() → void`: removes only `hikmat_courses_cache`, `hikmat_structure_cache` and `hikmat_testbank_cache`.
- `save() → boolean`: true when localStorage kept it. It always calls `mirrorState(key, json)`, which is a no-op stub until Task 4.
- `retireAccounts` writes the guest fold through `writeLocal`, and keeps `hikmat_current` when that write fails, so the fold is retried on the next launch.

- [ ] **Step 1: Failing tests.**
  - **S1 `storage_full_drops_caches`** (full-storage): `setStars(ck,lk,"learn",3); save()` returns true. After a reload the star is there, the three cache keys are gone, and every `hikmat_state_*` key is still present.
  - **S2 `storage_full_no_caches`**: filler only, up to quota. `save()` returns false, there's no `pageerror`, and a lesson still plays.
  - **S3 `retire_under_full_storage`**: retired-account plus filler to quota. After boot, either `hikmat_current` is still present or the guest key holds S-0042's stars.
- [ ] **Step 2: Run them.** Expected: S1 fails with `QuotaExceededError` from `save`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** everything. Expected: S1–S3 and all of Task 1 pass.
- [ ] **Step 5: Commit:** "A full phone no longer throws away the stars she just earned".

### Task 3: Content caches → IndexedDB (rule 5)

**Files:** in `index.html`: `loadFromBackend` (~6597–6700) and `loadTestBank` (~1970, ~2126).

**Interfaces (produces):**
- `contentStore.get(key) → Promise<any|null>` and `contentStore.set(key, value) → Promise<boolean>`.
  - The database is IndexedDB `hikmat_content` v1, store `kv`, keyed by the old localStorage key names.
  - Check first that `hikmat_content` is used nowhere else. Never touch `hikmat_capture_db`.
  - If IndexedDB is missing or throws, fall back to `writeLocal` and `localStorage.getItem`.
- `contentReady: Promise<void>`: moves each of the three keys once. Parse it, `set`, and only after `set` resolves true, `removeItem`. `hikmat_settings_cache` stays in localStorage.

- [ ] **Step 1: Failing tests.**
  - **C1 `caches_move_to_idb`**: after `contentReady`, localStorage has none of the three keys and IndexedDB has all three, unchanged.
  - **C2 `offline_uses_idb_cache`**: relaunch with `hikmat.api.*` aborted and `curriculum.json`/`testbank.json` returning 404. `COURSES.length` equals the cached length, and `LEVEL_BANK.bank.length > 0`.
  - **C3 `idb_blocked_falls_back`**: an init script makes `indexedDB.open` throw. Content still loads, and there's no `pageerror`.
  - **C4:** R on `full-storage` and `v7-midway` after the move.
- [ ] **Step 2: Run.** Expected: C1 and C2 fail.
- [ ] **Step 3: Implement.** Reads await `contentReady` and then `contentStore.get`, and writes go through `contentStore.set`. The layer order stays the same: cache, then `curriculum.json`, then the live API.
- [ ] **Step 4: Run** everything. Expected: PASS.
- [ ] **Step 5: Commit:** "Lesson caches move out of her progress's space".

**As built (4 Nov):**
- C3 also has a second case, C3b, where IndexedDB opens but refuses every write. The caches must stay in localStorage, because a cache leaves only once IndexedDB has committed it.
- C4 runs inside C1: v7 is opened on what the move left.
- S1 now runs with IndexedDB blocked. With IndexedDB working, the launch moves the caches out and that alone makes room, so a full phone still holding its caches at her next save exists only where IndexedDB is missing.
- S4 now has two cases. S4 is IndexedDB working, and the server's lessons must land in IndexedDB. S4b is IndexedDB blocked, which is the old S4; the bare-setItem sabotage now targets S4b.
- New self-test sabotages: caches copied but never removed (C1), and a store that is never read back (C2).
- The harness compares cache values as raw strings. STORAGE_JS reports other keys as lengths, and JS and Python count lengths differently for emoji.
- Self-test, added on Vishal's review: a cache that leaves localStorage before IndexedDB confirms the write must fail C2. The star-set-to-0 sabotage still fails I1 and I2.
- `e2e-levels.py` needs a signed-up learner, and v8 has accounts off. Its two checks that still hold now run in this harness for a learner with no account, as `level_test_promotes`:
  - LT1: passing the level-1 test promotes her, and the pass is stored and survives a relaunch.
  - LT2: nothing locks.
  - Sabotages: a level wall must fail LT2, and an unstored pass must fail LT1.
- `e2e-levels.py` stays for the web build on main and nightly, which still signs learners in, until step 5. On an accounts-off build it says SKIP. Step 3 replaces the ladder, and these tests with it.

### Task 4: Native mirror, boot snapshot, launch merge, erase marker, backup rules (rules 2, 6, 8)

**Files:**
- `index.html`: top of the main game script; the state section (~1829–1942); the erase handler in `openProgressSheet` (~3617); boot (~8870).
- `android-app/android/app/src/main/res/xml/backup_rules.xml` and `data_extraction_rules.xml`: add `<include domain="sharedpref" path="CapacitorStorage.xml" />` to every section, keeping the two files in step, plus a header note saying it holds the same anonymous state plus the undo copy.

**Interfaces (produces):**
- **`BOOT_STATE`** is the very first statement of the main game script, before `retireAccounts`, `loadState` or anything else that touches storage. It is `{ [key]: true|false }` for every `hikmat_state_*` key, true when the raw value parses to an object. `localWasMissing = !BOOT_STATE[stateKey()]`.
- `mergeSavedState(into, from, opts?)`: `opts.sameLearner` makes `attempts = max`. Everything else is unchanged.
- `mergeCopy(local, other, localWasMissing) → state`:
  - If `localWasMissing`, `other` is the base, settings included, and `local`'s gains are merged in with `sameLearner`.
  - Otherwise, the copy with the higher `eraseGen` wins outright.
  - If they're equal, `mergeSavedState(local, other, {sameLearner:true})`.
- `nativePrefs() → plugin|null`, via `capPlugin("Preferences")`.
- **Mirror gate:** `mirrorState(key, json)` writes nothing until `nativeRestore` has read the native copy. Until then it only remembers the latest call. After the gate opens, at most one write is in flight and the latest one wins.
  - `mirrorNow(key, json) → Promise<boolean>` first awaits `nativeRestore`.
  - If the read fails twice, the gate stays shut for the session, and local storage alone carries on.
- **`nativeRestore: Promise<boolean /* state changed */>`** starts right after `let state = loadState()`.
  1. If `window.Capacitor` is present without the plugin, wait for `load` and up to 2 s for the plugin.
  2. Read the native copy of `stateKey()` and apply `mergeCopy`.
  3. Open the gate, then write the merged state to both copies, which also seeds a missing native copy.
  4. If anything changed, apply changed settings the way the theme and language toggles do, call `refreshTop()`, and re-render the current screen with `rerender()`. A first-run screen goes to the root screen.

- [ ] **Step 1: Failing tests** (native mode unless noted).
  - **N1 `mirror_follows_save`**: within 2 s of `save()`, the native value equals the localStorage value.
  - **N2 `first_launch_seeds_mirror`**: v7-midway with native empty. After `nativeRestore`, native equals local.
  - **N3 `empty_webview_restored`**: native holds v7-midway and localStorage is empty. After launch, `SNAP` equals v7-midway's, settings included; localStorage is rewritten; and no first-run screen is left open.
  - **N4 `native_lower_never_wins`**: native has fewer stars and lower coins. The local values are kept, and native is raised to them.
  - **N5 `relaunch_does_not_inflate`**: three launches leave `attempts`, coins and `testSeen` lengths unchanged.
  - **N6 `erase_stays_erased`**: erase, with `drop_next_write` on. Until Task 6 that means `#progWipe` twice; after it, the confirm sheet. After a relaunch the total is 0 and `eraseGen` is 1. After another normal relaunch it is still 0.
  - **N7 `full_storage_native_carries`**: S2, then relaunch. The star is in `state`.
    - **N6b `erase_with_waiting_fold_and_stale_mirror`**: the 232-star bug was a merge that ran after an erase, and the launch merge is the same kind of merge, so all three happen at once here.
      - Setup: the S5 phone (retired-account, full, with nothing to drop, so the fold waits and `hikmat_current` stays). Native holds a stale copy of the guest profile from before the erase, with its stars and `eraseGen` 0. `drop_next_write` is on.
      - Erase through the menu, then free room and relaunch twice.
      - Both launches show 0 stars and 0 gems, with gender Male and mascot kabir. `eraseGen` is at least 1 in local and, after the second launch, in native too. Neither the account's 232 stars nor the stale mirror's stars come back.
      - If neither local nor native took the erase, she is told it failed ("Could not erase. Try again."), never "Progress erased", and the next launch shows her stars as before.
      - Self-test sabotages: a `mergeCopy` that ignores `eraseGen` must fail N6b, and so must an erase that skips `endAccountFold()`.
  - **N8 `boot_save_cannot_clobber_native`**:
    - Setup: native holds v7-midway with `get_delay_ms=1500`. localStorage is the retired-account fixture without `hikmat_state_guest`.
    - During the delay, `retireAccounts` folds S-0042 into a new guest key, and the test calls `save()` too. Assert that the `NATIVE` log shows no `set` before the `get` resolved.
    - After restore: every star is ≥ both v7-midway and S-0042; settings, gender and mascot equal v7-midway's (native won as base); and native equals local.
  - **N9 `boot_save_on_empty_webview`**: N8 starts from a WebView that still holds an account, so this is the plain case.
    - Setup: localStorage completely empty (the WebView's data was wiped), native holds v7-midway with `get_delay_ms=1500`.
    - During the delay, boot writes whatever a first launch writes, and the test calls `save()` as well.
    - Assert: `BOOT_STATE` was recorded before the first `setItem` (an init script logs the order), so `localWasMissing` is true. The `NATIVE` log shows no `set` before the `get` resolved. After restore, `SNAP` equals v7-midway's, settings included, native equals local, and no first-run screen is left open.
    - Self-test sabotage: a mirror gate that opens before the read must fail N9.
  - **M1:** `mergeSavedState(a, clone(a), {sameLearner:true})` deep-equals `a`.
  - **M2:** without opts, attempts still add up.
  - **M3:** `mergeCopy` follows the eraseGen rules in both directions.
  - **W1 (web):** no Capacitor, so `nativeRestore` resolves false and there's no `pageerror`.
- [ ] **Step 2: Run.** Expected: N1–N9 (with N6b) and M3 fail, and M2 and W1 pass.
- [ ] **Step 3: Implement.** The erase handler sets `eraseGen + 1`, calls `save()`, and awaits `mirrorNow` before it says "Progress erased."
- [ ] **Step 4: Run** everything in both modes. Expected: PASS.
- [ ] **Step 5: Commit:** "Every save gets a second copy outside the WebView, and it can't overwrite one it hasn't read".

**As built (5 Nov):**
- `mergeCopy` compares `eraseGen` first, also when `localWasMissing`. Only with equal `eraseGen` does `localWasMissing` pick the native copy as the base. Whatever localStorage holds by the time the native read returns came from this boot (an account fold that landed on an empty WebView, say), and that is older than an erase the native copy has already seen. N6b (b) is that case.
- A state the launch merge puts in place gets the same boot defaults as a loaded one (`withBootDefaults`).
- The "neither copy took the erase" case is its own test, **N6c `erase_refused_everywhere`**: the S7 phone, with the fake Preferences refusing every write (`refuse_writes`). The erase handler waits up to 5 s for `mirrorNow`, which counts the native copy only once it reads back as the erased state. If neither copy took it, the state and the waiting fold go back as they were, the sheet stays open, and she sees "Could not erase. Try again." / "मिटाया नहीं जा सका। फिर से कोशिश करो।" A second tap while the erase is under way does nothing. The Hindi line goes into Task 6's Hindi check with the other new lines.
- Self-test sabotages: a mirror gate that opens before the read (N9), a `mergeCopy` that ignores `eraseGen` (N6b), Start fresh that leaves a waiting fold in place with a stale native copy (N6b (a)), and a native copy that simply replaces hers (N4, which must name the gems).
- N4 lists a gem drop before the star drops, so its clipped report always shows it.
- The harness's own Preferences probe checks that `harness_probe` is among the keys, not the only one, since the app's mirror writes are there too.
- Run on 5 Nov: 291 passed, 0 failed, web and native, with the self-test.

### Task 5: Save my progress / Bring my progress (rule 7)

**Files:** in `index.html`, `openProgressSheet` (~3617): add `#progSave` and `#progBring` buttons, each with `min-height:48px`. Add a hidden `<input type="file" id="progFile">` with no `accept`, because Android pickers hide WhatsApp downloads saved as octet-stream.

**Interfaces (produces):**
- `progressFileText(state) → string`, the envelope from Task 1: `{kind:"bodhya-progress", v:1, app, savedAt, state}` and nothing else. **One learner only:** `state` is the profile on screen. No other `hikmat_state_*`, no `hikmat_current`, no account, name or student id goes in. File name: `bodhya-progress-YYYY-MM-DD.json`, and it never holds anyone's name.
- `starsIn(state) → int`: her star total by the same rule as `totalStars()`.
- `readProgressFile(text) → {state} | {error:"toobig"|"notjson"|"notours"}`. The limit is 2 MB, checked on `file.size` before reading. It accepts any `v ≥ 1` and reads only known fields.
- `cleanImportedState(obj) → state`:
  - Every key is a string of at most 140 characters, and `__proto__`, `constructor` and `prototype` are dropped at every level.
  - Stars become integers clamped to 0–3. Coins become a finite integer, 0–10,000,000.
  - `levelTests[n]` becomes `{passed:bool, bestPct:0–100, attempts:int}`.
  - `testSeen` becomes arrays of strings, capped at 5000 entries each. `seenTypes` and `videosSeen` become flag maps.
  - `lastLoc` and `lastMode` are type-checked.
  - Settings are allow-listed (lang `en|hi`, theme `light|dark`, sound bool, gender `Female|Male|Other`, mascot a known id, prefsTouched bool).
  - `eraseGen` is ignored.
- `saveProgressFile() → Promise<boolean>`.
  - Android: `Filesystem.writeFile({path, data, directory:"CACHE", encoding:"utf8"})`, then `Share.share({title, files:[uri]})`.
  - Web: `navigator.share({files})` when `navigator.canShare({files})` allows it, otherwise an `<a download>`.
- `bringProgressFile(file: File) → Promise<{ok, stars?, error?}>`:
  1. Read and clean the file. A bad file gets the bad-file message, and nothing else happens.
  2. **Ask first.** A confirm sheet (`#bringWrap`) says how many stars the file holds (`starsIn(clean)`), with "Bring it in" (`#bringYes`) and "Not now" (`#bringNo`, last, under her thumb). Not now changes nothing.
  3. On yes: `mergeSavedState(clone(state), clean, {sameLearner:true})`. **D5:** the file's settings (lang, theme, sound, gender, mascot, prefsTouched) are taken only when she has no progress yet (no star, no gem, no level test passed). Otherwise hers stay. **`eraseGen` in the file is ignored:** bringing a file in is her own choice, so her `eraseGen` stays as it is.
  4. Then `save()`, await `mirrorNow`, `refreshTop()` and a re-render. If neither copy took it, nothing changes and she is told so.
- **Copy (EN / HI):**

| Where | English | Hindi |
| --- | --- | --- |
| Save button | Save my progress | अपनी प्रगति सहेजो |
| Save button, second line | A small file to keep on WhatsApp, Bluetooth or Files | एक छोटी फ़ाइल, जिसे WhatsApp, ब्लूटूथ या Files में रखा जा सकता है |
| Bring button | Bring my progress | अपनी प्रगति वापस लाओ |
| Bring button, second line | Nothing you have now is lost | अभी का कुछ भी नहीं मिटेगा |
| Confirm title | Bring in this progress? | यह प्रगति वापस लानी है? |
| Confirm body | This file has N stars. You keep the best of this file and this phone. | इस फ़ाइल में N सितारे हैं। फ़ाइल और इस फ़ोन में से जो बेहतर है, वही रहेगा। |
| Confirm yes | Bring it in | वापस लाओ |
| Confirm no | Not now | अभी नहीं |
| After a good file | Progress brought back: N stars | प्रगति वापस आ गई: N सितारे |
| When bringing fails | Could not bring it in. Try again. | वापस नहीं लाया जा सका। फिर से कोशिश करो। |
| After any bad file | This is not a Bodhya progress file. | यह बोध्या की प्रगति वाली फ़ाइल नहीं है। |
| After saving | File saved. Keep it safe. | फ़ाइल सहेज ली गई। इसे संभालकर रखो। |
| When saving fails | Could not save the file. Try again. | फ़ाइल सहेजी नहीं जा सकी। फिर से कोशिश करो। |

- [ ] **Step 1: Failing tests.**
  - **F1 `save_bring_round_trip` (web):** Save produces a download whose envelope `state` deep-equals local. Bringing it in a fresh context shows the star count, and after yes gives the same `SNAP`, settings included.
  - **F1b `file_holds_one_learner`:** on the retired-account phone (S-0042's account and name still stored), the saved file's top-level keys are exactly the envelope's, its text holds no student id, no name and no other `hikmat_state_*`, and the file name matches `^bodhya-progress-\d{4}-\d{2}-\d{2}\.json$`.
  - **F1c `bring_asks_first`:** the confirm sheet says the file's star count. Not now leaves `SNAP` and both stores unchanged.
  - **F1d `bring_settings_d5`:** a file brought onto a phone with progress keeps her settings. The same file on a phone with no progress takes the file's settings. A file with `eraseGen` 7 leaves hers as it was, in both cases.
  - **F2 (native):** Save calls fake `writeFile` with `directory:"CACHE"` and the file name pattern, and fake `share` with that uri.
  - **F3 `bring_never_lowers`:** bringing a lower file lowers nothing. Bringing the same file twice leaves the second `SNAP` equal to the first.
  - **F4 `bring_rejects_bad_files`:** PNG bytes, cut-off JSON, `{"foo":1}`, the wrong `kind`, a 3 MB file, and `__proto__`/`constructor` keys with stars "3", 99, -1 and NaN. Each leaves the state unchanged or cleaned, keeps `({}).polluted === undefined`, shows the message, and raises no `pageerror`.
  - **F5:** a file saved before an erase, brought in after it, restores the stars, and they survive two relaunches with the mirror on.
  - **F6 `bring_any_name_or_type`:** the file picked as WhatsApp and Android pickers hand it over: type `""` or `application/octet-stream`, and names like `bodhya-progress-2026-11-05 (1).json` or `bodhya-progress-2026-11-05` with no extension. All are brought in.
  - **F7 (manual, Android):** the real path. Save on emulator A, send the file over WhatsApp, open it on emulator B, and bring it in there. Most girls will move it that way. Record the steps and the result under "As built".
- [ ] **Step 2: Run.** Expected: FAIL (no `#progSave`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** everything.
- [ ] **Step 5: Commit:** "She can carry her progress to a new phone as a small file".

**As built (5 Nov):**
- The tests live in seven harness tests, in both modes: `save_bring_round_trip` (F1, F1b), `save_hands_over_file` (F2; on the web through Web Share, in the app through Filesystem and Share, where a share she cancels is not called saved), `bring_asks_and_follows_d5` (F1c, F1d), `bring_never_lowers` (F3), `bring_rejects_bad_files` (F4), `bring_after_erase` (F5) and `bring_any_name_or_type` (F6).
- Every message in the My progress sheet carries `data-msg` (`saved`, `savefail`, `brought`, `bringfail`, `notours`, `erasewarn`, `erasefail`), so the tests check which message showed in either language.
- Bring reads the file with `File.text()` where it exists and `FileReader` where it does not (WebView before Chrome 76). F6 runs once with `Blob.prototype.text` deleted.
- The confirm sheet has a 🔊 button (48 px) that reads its title, body and both buttons. The 🔊 line and the share-sheet title ("My Bodhya progress" / "मेरी बोध्या प्रगति") are new strings for Task 6's Hindi check.
- Self-test sabotages: a brought file that replaces her state (F3), a bring that does not ask (F1c), the file's eraseGen taken (F1d), the file's settings always taken (F1d), a file that is not cleaned (F4: it really does pollute `Object.prototype` through `mergeSavedState`), and a file that holds every profile on the phone (F1b).
- Checked at 360x640 in Hindi light and dark, and in English: nothing overflows sideways. Save and Bring are 81-103 px tall. Start fresh and Close are 46 px in English; Task 6 rebuilds that sheet and must bring them to 48. With the "brought back" message showing, the sheet is taller than the screen and scrolls, and its × stays pinned.
- The three plugins are installed now (`--save-exact`, Task 7's first step brought forward), because F7 needs them on a device. None adds a permission.
- **F7, done on two Android 15 emulators (5 Nov), without WhatsApp:**
  1. A: 487 stars and 1,240 gems, in both copies after a relaunch.
  2. My progress, Save (a real tap). The Android share sheet opened with `bodhya-progress-2026-11-05.json`; its targets were Quick Share, Drive and Gmail. The file in the app's cache is the envelope alone, with no name, id or other profile. Backing out of the share sheet showed no "saved".
  3. The file was put where WhatsApp keeps received documents on B: `Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Documents/`.
  4. B (nothing played): My progress, Bring (a real tap). Android's picker opened with every type allowed. The file was **not** in Recent, Documents or search. It was reachable through ☰, the device, Android, media, com.whatsapp, WhatsApp, Media, WhatsApp Documents: 7 taps deep.
  5. Picking it showed "This file has 487 stars". Bring it in: 487 stars and 1,240 gems in both copies, her settings from the file (D5), eraseGen 0, the same after two relaunches.
  - **Still to do:** the WhatsApp hop itself. The emulators have no Play Store, and WhatsApp needs a phone number that receives a code. See Q4.
  - Emulator notes: `hikmat_test` cannot start (its API 34 image is not installed); `bodhya_b35` was made from the Android 15 image for this. `eb_api26` (Android 8) has WebView 58, where the game does not run at all: it uses object spread, which needs Chrome 60, and so did v7.

### Task 6: Start fresh confirm sheet and the 7-day undo (rule 11)

**Files:** in `index.html`: `openProgressSheet` (~3617), a new `openEraseSheet(fromRect)`, and boot.

**Layout:**
- **My progress sheet:** chips; message box (`#progWarn`); Save; Bring; "Bring back what I erased" (only while an undo copy is live); Close; then, set apart by 24 px at the very end, Start fresh (`#progWipe`, danger). Tapping it no longer arms itself. It opens the confirm sheet.
- **Confirm sheet** (Figma 23:244), bottom-anchored like every sheet: title; body; undo note; Erase (`#eraseYes`, quiet red outline, full width); a 32 px gap; then "Keep it" (`#eraseKeep`, the main button, full width) last, under her thumb.
- Erase ignores taps for 600 ms after the sheet opens.
- Keep it closes the confirm sheet and leaves the My progress sheet as it was.
- The 🔊 button reads title, body and undo note.

**Interfaces (produces):**
- `undoKey() → "hikmat_undo_" + (currentStudent && currentStudent.id || "guest")`.
- `readUndo() → Promise<undo|null>`: reads localStorage, else native. An expired copy (`Date.now() > until`) is removed from both stores and returns null. Once `nativeRestore` has finished, boot calls `readUndo()` once to sweep expired copies.
- `eraseProgress() → Promise<boolean>`:
  1. Write the undo copy of the current state (`until = erasedAt + 7*864e5`) to localStorage with `writeLocal`, and await it to native with `mirrorNow`. If neither store took it, stop and say the erase failed.
  2. Then `state = Object.assign(fresh(), keep, {eraseGen: (state.eraseGen||0)+1})`, `save()`, await `mirrorNow`, and say "Progress erased…".
- `restoreErased() → Promise<boolean>`: `restored = mergeSavedState(clone(state), undo.state, {sameLearner:true})`. That keeps the current settings and anything earned since the erase. Then `restored.eraseGen = max(state.eraseGen, undo.state.eraseGen) + 1`, save and await the mirror, then remove both undo copies and say "Your progress is back…".
- **Copy (EN / HI):**

| Where | English | Hindi |
| --- | --- | --- |
| Confirm title | Erase your progress? | अपनी प्रगति मिटानी है? |
| Confirm body | Your stars, lessons and gems on this phone will be erased. Your settings stay. | इस फ़ोन से तुम्हारे सितारे, पाठ और रत्न मिट जाएँगे। तुम्हारी सेटिंग वैसी ही रहेगी। |
| Undo note | You can undo this for 7 days. | 7 दिन तक इसे वापस लाया जा सकता है। |
| Erase button | Erase | मिटाओ |
| Keep button | Keep it | रहने दो |
| After erasing | Progress erased. You can bring it back for 7 days. | प्रगति मिटा दी गई। 7 दिन तक इसे वापस लाया जा सकता है। |
| Erase failed | Could not erase. Try again. | मिटाया नहीं जा सका। फिर से कोशिश करो। |
| Undo row | Bring back what I erased | मिटाया हुआ वापस लाओ |
| Undo row, second line | N days left / Last day | N दिन बाकी / आज आख़िरी दिन |
| After undo | Your progress is back: N stars | तुम्हारी प्रगति वापस आ गई: N सितारे |
| Undo failed | Could not bring it back. Try again. | वापस नहीं लाया जा सका। फिर से कोशिश करो। |

- [ ] **Step 1: Failing tests** (both modes unless noted).
  - **U1 `erase_confirm_layout`:**
    - At 360×640, 393×852 and 1280×800, in English and Hindi, measure `#progWipe`, then tap it.
    - `#eraseYes` must not intersect `#progWipe`'s rect grown by 16 px, and `#eraseKeep` must contain `#progWipe`'s centre.
    - A real double tap, two clicks 120 ms apart at `#progWipe`'s centre, erases nothing.
  - **U2 `erase_guard`:** a click on `#eraseYes` within 600 ms does nothing; after that it erases.
  - **U3 `undo_round_trip`:**
    - Erase. Undo copies are in local and native, with `until` about now plus 7 days.
    - Relaunch. The undo row shows "7 days left".
    - Restore. `SNAP` equals the pre-erase `SNAP` (settings excepted), `eraseGen` is 2, and both undo copies are gone. That holds after another relaunch.
  - **U4 `undo_survives_webview_wipe` (native):** erase, clear localStorage, relaunch. The undo row is there, read from native, and restore works.
  - **U5 `undo_expires`:** erase, then relaunch with `Date.now` moved 8 days ahead by an init script. Both undo copies are removed and there's no undo row. At 6 days ahead the row says "1 day left".
  - **U6 `restore_beats_stale_native` (native):** restore with `drop_next_write`, then relaunch. The restored stars are kept.
  - **U7 `restore_keeps_new_play`:** erase, earn a star in a new lesson, restore. The old stars and the new one are both there.
  - **U8:** R after erase and after restore. v7 reads both states without error, and the totals match.
  - **N6** now goes through the confirm sheet.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** everything. Then take screenshots at 360×640 in light, dark and Hindi, saved to `apps/hikmat/docs/pr-screens/step-0/`:
  - the My progress sheet
  - the confirm sheet
  - the My progress sheet with the undo row

  Check that nothing wraps badly in Hindi and that every button is at least 48 px tall.
- [ ] **Step 5: Commit:** "Start fresh asks on its own sheet, and an erase can be undone for 7 days".

**As built (5 Nov):**
- **Layout against Figma 23:244.** Kept from Figma: title, body, undo note, the outlined quiet-red Erase, Keep it as the main button, both 56 px tall. Kept from the plan: the 32 px gap between Erase and Keep it (Figma has 14 px; 32 is the reviewed, safer distance from the button under her thumb), and the plan's body copy (Figma's names a learner, and with accounts off there are no names). The buttons keep the app's pill shape.
- **Keep it, and Bring it in on the Task 5 sheet, use navy text on the saffron gradient** (Figma draws Keep it that way): 6.1:1 to 8.3:1, where white is 2.0:1 to 2.7:1 and fails AA. Every other `.btn.primary` in the app still has white text: that is older than v8 and outside this step's "no visual change" rule (noted under Not in this step).
- After an erase that is kept, My progress opens again, with "Progress erased…" and the undo row in it, so she sees what happened and where to bring it back from. Before, the sheet closed and only the screen reader was told.
- **The undo copy and a waiting account fold.** A waiting fold's stars are never in her profile; they stay in the account's own key, which nothing deletes. The erase ends the fold, so the undo copy records which account (`fold: {id, gender, mascot}`, never the name), and the restore folds that account's stars back once, as the fold would have. **U9 `undo_brings_back_waiting_fold`** (native) tests this, and `_fold_landed` checks that its attempts are counted once through two more launches.
- **On the web, a phone with no room for the undo copy refuses Start fresh** ("Could not erase. Try again."), as the plan's rule says: there is no native store to hold it. S5 now checks exactly that in web mode, and its full case runs in native mode, where its two older sabotages are now judged.
- The fake Preferences can now lose the next write to one key (`drop_next_write = "<key>"`). N6 and N6b (a) lose the erase's own write, not the undo copy written just before it.
- **U6** loses every native write of the restore (`refuse_writes`). One lost write is not enough: `mirrorNow` writes again and reads back, so a single drop heals and the native copy is never stale.
- **U5** expects "Last day" six days on, as the copy table says, not "1 day left" as the test line said. Days left are rounded up: 7 just after the erase, "Last day" in its final 24 hours.
- U1 and U2 run in web mode only. They measure layout and tap timing, which the native fake does not touch. U3, U5, U7 and U8 run in both modes; U4, U6 and U9 are native.
- Self-test sabotages: no 600 ms guard (U2), Erase and Keep it swapped (U1), a restore that replaces her state (U7), a restore that keeps the undo copy's eraseGen (U6), an undo copy never removed (U5, as a prelude: on the web the launch's sweep runs before any script appended after the game's), an undo copy kept only in localStorage (U4), and one that forgets the ended fold (U9).
- Screenshots in `docs/pr-screens/step-0/`, 360x640, English and Hindi, light and dark: My progress (top and end), the confirm sheet, and My progress with the undo row. Every button is at least 48 px; nothing overflows sideways; Erase is 6.5:1 (light) and 8.5:1 (dark), the undo note 9.4:1 and 8:1.
- Hindi checked for every new line of Tasks 4 to 6: imperatives and passives only, none addressing her as masculine.

### Task 7: Android wiring, v8 build, checks, closed testing, PR

**Files:**
- `android-app/package.json`: exact versions of the three plugins.
- `android-app/android/app/build.gradle`: versionCode 8.
- `android-app/build.sh`: run `e2e-progress-safety.py --mode both` first, and stop if it fails.
- `hikmat/public/sw.js`: `CACHE`, one step up.
- `Hikmat Games/CLAUDE.md`: the Android section (plugins, mirror, undo, backup rules, the e2e gate).
- `playstore/`: v8 release notes and `v8-closed-test-check.md`.

- [x] **Install and build.** The three plugins were installed with `--save-exact` in Task 5. Run `npx cap sync android`, then `android-app/build.sh`. Expected: e2e PASS, then the signed AAB.
- [x] **Move the release token.** game.html changed in this PR, so `CACHE` in `hikmat/public/sw.js` goes one step up (`hikmat-pwa-v13` → `v14`), once, in its own commit. The same step is in `SECURITY.md`, "Every web deploy", for any later web deploy.
- [x] **Check the AAB.**
  - `jarsigner -verify` passes, and the SHA-256 is `2A:6C:…:DC:26`.
  - versionCode is 8, and the manifest's only permission is INTERNET.
  - The `Preferences`, `Share` and `Filesystem` plugin classes are present.
  - `base/assets/public/index.html` contains no `chat.whatsapp`, no `youtube` and no live `ai_ask`.
- [x] **Check on the emulator.**
  1. Install v7, built from `play-v7` with the upload key, and earn some stars.
  2. Install v8 over it. The stars are intact.
  3. Save. The share sheet opens, and the file lands in Files.
  4. Bring that file. It merges.
  5. Erase, then undo.
  6. On a debug build: `run-as … rm -rf app_webview`, then relaunch. The stars come back from the mirror.
- [ ] **Closed testing first.**
  - Vishal uploads v8 to the closed testing track only.
  - `v8-closed-test-check.md` asks 2 or 3 tester phones to note the My progress numbers before updating, check after updating that they are the same or higher and that played lessons are open, then Save my progress and send the file, with a parent's OK.
  - Those files go into `tests/saves/private/`, and the harness must pass on them before v8 goes anywhere else.
- [ ] **The WhatsApp hop (F7) on two real tester phones.** Save on phone A, send the file over WhatsApp, and bring it in on phone B through the in-app picker. Note how many taps the picker takes to reach it, and whether it shows under Recent or Documents.
- [ ] **What WhatsApp hands over (for "Open with Bodhya", Q5).** On the same phones, tap the received file in WhatsApp and record the intent it sends: action, MIME type, file name and URI scheme. A build with a broad filter that only logs what arrives, installed on the test phones alone, shows it. The release filter then matches only that.
- [ ] **Open the PRs.** Push and open the PRs: `fix/topbar-long-back-label` → `nightly`, then `safety/step-0`, with the screenshots, test output and android-app diffs. Web: no deploy.


**As built (5 Nov), the parts that need no phone:**
- **Release token:** `CACHE` is `hikmat-pwa-v14`, in its own commit.
- **versionCode 8, and versionName "8".** v7 and every release before it said "1.0", so a tester could not tell from Settings → Apps whether the update had landed. versionName is a label only; package, key, scheme and hostname are unchanged.
- **Signing.** `keystore.properties` lives beside the upload key in `~/code/Hikmat Games/android-app/android/`, not in this repo. `build.gradle` reads it by path from `BODHYA_KEYSTORE_PROPERTIES`, which `build.sh` sets; its `storeFile` is absolute. Nothing is copied into the repo.
- **`build.sh`** stops unless `index.html` and `hikmat/public/game.html` are byte-identical (the harness tests one, the AAB ships the other), then runs `e2e-progress-safety.py --mode both` and builds nothing if it fails. `SKIP_E2E=1` is only for rebuilding a tree that just passed. The build run: 335 passed, 0 failed (626 s), then the signed AAB.
- **The v7 AAB is kept** as `playstore/hikmat-release-v7.aab`, because `build.sh` overwrites `hikmat-release.aab`.
- **The AAB:**
  - `jar verified`, signer SHA-256 `2A:6C:58:…:DC:26`;
  - versionCode 8; permissions INTERNET and androidx's own `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`, the same two as v7;
  - no new intent filter;
  - the Preferences, Share and Filesystem plugin classes are in the dex and in `capacitor.plugins.json`;
  - `capacitor.config.json` has `androidScheme` https and `hostname` localhost;
  - `base/assets/public/index.html` equals `www/index.html`, with no `chat.whatsapp` and no `youtube`. Its three `ai_ask` hits are comments and the cut call (`const r = null`), the same three as v7.
- **Emulator** (Android 15, WebView 124). Both AABs were made into APKs by bundletool and signed with the same local debug key. Each step was judged with the harness's own `SNAP_JS` and a field-by-field comparison against v7's state.
  1. v7, seeded through its own `save()` and relaunched: 487 stars, 1,240 gems, level 2, 40 played lessons open, at `https://localhost`.
  2. v8 installed over it as an in-place update (first install time kept): nothing lost or lowered, and the native copy equals localStorage.
  3. Save my progress opened the share sheet with `bodhya-progress-2026-11-05.json`. The emulator's sheet offers only Quick Share, Drive and Gmail, all of which need an account, and Files takes no shares. So the cache file Bodhya handed over (4,091 bytes, 487 stars, no eraseGen) was put in Downloads.
  4. Bring my progress: the picker showed the file first under Recent (1 tap). The confirm said "This file has 487 stars", and Bring it in lowered nothing.
  5. Start fresh, then Erase: 0 stars, eraseGen 1, the undo copy in both stores, and "7 days left", still there after a relaunch. Bring back what I erased: 487 stars, eraseGen 2, the undo copy gone from both stores, nothing lost.
  6. A debug build of the same tree installed over it. After `run-as … rm -rf app_webview`, the next launch had 487 stars, level 2 and 40 played lessons from the native copy, and the launch after it too.
- **Closed-test checklist:** `playstore/v8-closed-test-check.md`, run without Claude. It has a record sheet per tester phone: Android and WebView versions; stars before and after the update from v7; the WhatsApp hop (name and type WhatsApp shows, taps to find the file, whether "Open with Bodhya" appeared); Start fresh then Undo, before and after.
  - The receiving phone is Vishal's, starting at 0 and erased between testers, because Bring merges into the phone's own progress.
  - Start fresh comes after the hop, so her sent file is a way back if Undo fails.
  - What WhatsApp hands over comes from `adb logcat` on the receiving phone (action, type, URI scheme). A logging build is needed only if WhatsApp finds no app to open the file at all.
- `Hikmat Games/CLAUDE.md`, Android section: points at this repo's `android-app/` (the copy in Hikmat Games is the old checkout, kept only for the keystore), and covers the build gate, the plugins, where her progress lives, the backup rules and the upgrade check.

## Not in this step

- **White text on the saffron main button** (noted 5 Nov). `.btn.primary` puts white on a gradient from #ffa24d to #f47b20, 2.0:1 to 2.7:1, below AA. The v8 sheets use navy (`.btn.primary.ink`). Changing every main button is a visual change across the app, for the redesign.

- **A WebView too old for the game** (noted 5 Nov, for after v8). The game needs Chrome 60 (object spread, in v7 too); an Android 8 phone whose WebView never updated has 58 and shows a blank screen. Instead: a small script, run before the game's, that shows a plain message in English and Hindi, "Please update Android System WebView", with what to tap. Check Play Console (Android vitals, device catalog) for how many installs have a WebView older than 60.

- Key aliasing for renamed content: rule 10.
- Staged rollout: rule 12, at production.
- Lines for `bodhya.net/data-deletion`:
  - Files saved with Save my progress belong to her, so they have to be deleted from WhatsApp or Files.
  - Start fresh keeps an undo copy for 7 days.
  - Uninstalling, or Clear storage, removes everything at once.

## Open questions (answered 4 Nov)

- **Q1.** Should `android-app/` move into bodhya-learn, with no signing material? **Yes.** The keystores, `keystore.properties` and the credentials files stay outside git; the ignore list covers `keystore/`, `*.jks`, `*.keystore`, every `keystore.prop*`, `local.properties`, `google-services.json`, `node_modules/`, `build/`, `.gradle/` and `android/app/build/`.
- **Q2.** Should `"hostname": "localhost"` be pinned in `capacitor.config.json`? **Yes**, with `"androidScheme": "https"` kept explicit. Both are today's values, so the origin, and the localStorage under it, does not change.

## Open questions (5 Nov, answered the same day)

- **Q3.** `npm audit` reports a critical advisory for `@capacitor/android` 8.3.5 to 8.4.2 (GHSA-rvm3-566m-v7fv: remote content can be loaded at the app origin through the internal HTTP proxy path). The app is on 8.4.1, and 8.4.3 fixes it. Should v8 move `@capacitor/android`, `core` and `cli` to 8.4.3? The scheme and hostname stay as they are. **Yes**, the smallest change that closes it, with `core` and `cli` in step. The fix touches the app origin her progress lives under, so after the bump the upgrade from the real v7 AAB to v8 is rerun on the emulator: stars, level and played lessons must all still be there.
  - **Done 5 Nov.** `@capacitor/android`, `core` and `cli` are pinned at exactly 8.4.3. What changed from 8.4.1: the local server serves the CapacitorHttp proxy path only to fetch/XHR and only when CapacitorHttp is enabled (it is not in our config, so none of our requests used it), proxied responses get `Content-Security-Policy: sandbox`, and `launchIntent` refuses that path. `core` is identical apart from its version; `cli` differs only in its project templates. `npm audit` has no critical left; three highs remain (`@xmldom/xmldom`, `brace-expansion`, `tar`), all inside `@capacitor/cli`, which runs at build time and is not in the APK.
  - **Upgrade rerun on the Android 15 emulator:** the real v7 AAB from `playstore/` (versionCode 7, upload key `2A:6C:…:DC:26`) and the v8 release AAB, both made into APKs by bundletool and signed with the same local debug key. Only the certificate differs from a phone's update. v7 was given v7-midway through its own `save()` and relaunched: 487 stars, 1,240 gems, level 2 with the level-1 pass, 40 played lessons and none shut, Hindi, dark, Tara. v8 installed over it, data kept: the same on every count, at origin `https://localhost`. Her stored state lost no field and lowered no value, and the native copy written at the first launch equals localStorage. v8 still had versionCode 7 then, which Android installs as an in-place update, as it does 8.
- **Q4.** The WhatsApp hop of F7. WhatsApp needs a phone number that receives a code, and these emulators have no Play Store. Options: Vishal registers WhatsApp on emulator A with a test number and links emulator B as a companion device, or the hop is done on two real phones in closed testing (Task 7). **Real phones, in closed testing (Task 7).** WhatsApp is not registered on an emulator.
- **Q5.** In Android's picker, a file in WhatsApp's documents folder took 7 taps to reach (step 4 above). If a real WhatsApp download is no easier to find, should the app also open a progress file that she taps in WhatsApp ("Open with Bodhya")? That is an intent filter in the manifest, and a launch path that hands the file to the same Bring flow. **Yes.** First see on the real phones what WhatsApp actually hands over (file name and type), then match only that, so Bodhya never offers to open every JSON file on the phone. It goes through the same checks and the same "This file has N stars" confirm as Bring. The in-app picker stays as the fallback.
