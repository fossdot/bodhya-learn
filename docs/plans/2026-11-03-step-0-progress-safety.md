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
- D1–D6 are accepted. Q1 (android-app in git) and Q2 (pin hostname) are still open, so don't do Task 0 or pin the hostname.

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

### Task 0: put `android-app/` under git (blocked on Q1; skip)

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
  - The plugin API shapes come from the real `definitions.d.ts` in `/private/tmp/claude-501/-Users-fossdot-Developer-hikmat-bench/190a782b-b183-4ee4-841b-f39b7ff55bd3/scratchpad/plugin-src/capacitor-*/package/dist/esm/`.
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
  - **N8 `boot_save_cannot_clobber_native`**:
    - Setup: native holds v7-midway with `get_delay_ms=1500`. localStorage is the retired-account fixture without `hikmat_state_guest`.
    - During the delay, `retireAccounts` folds S-0042 into a new guest key, and the test calls `save()` too. Assert that the `NATIVE` log shows no `set` before the `get` resolved.
    - After restore: every star is ≥ both v7-midway and S-0042; settings, gender and mascot equal v7-midway's (native won as base); and native equals local.
  - **M1:** `mergeSavedState(a, clone(a), {sameLearner:true})` deep-equals `a`.
  - **M2:** without opts, attempts still add up.
  - **M3:** `mergeCopy` follows the eraseGen rules in both directions.
  - **W1 (web):** no Capacitor, so `nativeRestore` resolves false and there's no `pageerror`.
- [ ] **Step 2: Run.** Expected: N1–N8 and M3 fail, and M2 and W1 pass.
- [ ] **Step 3: Implement.** The erase handler sets `eraseGen + 1`, calls `save()`, and awaits `mirrorNow` before it says "Progress erased."
- [ ] **Step 4: Run** everything in both modes. Expected: PASS.
- [ ] **Step 5: Commit:** "Every save gets a second copy outside the WebView, and it can't overwrite one it hasn't read".

### Task 5: Save my progress / Bring my progress (rule 7)

**Files:** in `index.html`, `openProgressSheet` (~3617): add `#progSave` and `#progBring` buttons, each with `min-height:48px`. Add a hidden `<input type="file" id="progFile">` with no `accept`, because Android pickers hide WhatsApp downloads saved as octet-stream.

**Interfaces (produces):**
- `progressFileText(state) → string`, the envelope from Task 1. File name: `bodhya-progress-YYYY-MM-DD.json`.
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
- `bringProgressFile(file: File) → Promise<{ok, stars?, error?}>`: `mergeSavedState(state, clean, {sameLearner:true})`, applying D5 for settings, then `save()`, `refreshTop()` and a re-render.
- **Copy (EN / HI):**

| Where | English | Hindi |
| --- | --- | --- |
| Save button | Save my progress | अपनी प्रगति सहेजो |
| Save button, second line | A small file to keep on WhatsApp, Bluetooth or Files | एक छोटी फ़ाइल, जिसे WhatsApp, ब्लूटूथ या Files में रखा जा सकता है |
| Bring button | Bring my progress | अपनी प्रगति वापस लाओ |
| Bring button, second line | Nothing you have now is lost | अभी का कुछ भी नहीं मिटेगा |
| After a good file | Progress brought back: N stars | प्रगति वापस आ गई: N सितारे |
| After any bad file | This is not a Bodhya progress file. | यह बोध्या की प्रगति वाली फ़ाइल नहीं है। |
| After saving | File saved. Keep it safe. | फ़ाइल सहेज ली गई। इसे संभालकर रखो। |
| When saving fails | Could not save the file. Try again. | फ़ाइल सहेजी नहीं जा सकी। फिर से कोशिश करो। |

- [ ] **Step 1: Failing tests.**
  - **F1 `save_bring_round_trip` (web):** Save produces a download whose envelope `state` deep-equals local. Bringing it in a fresh context gives the same `SNAP`, settings included.
  - **F2 (native):** Save calls fake `writeFile` with `directory:"CACHE"` and the file name pattern, and fake `share` with that uri.
  - **F3 `bring_never_lowers`:** bringing a lower file lowers nothing. Bringing the same file twice leaves the second `SNAP` equal to the first.
  - **F4 `bring_rejects_bad_files`:** PNG bytes, cut-off JSON, `{"foo":1}`, the wrong `kind`, a 3 MB file, and `__proto__`/`constructor` keys with stars "3", 99, -1 and NaN. Each leaves the state unchanged or cleaned, keeps `({}).polluted === undefined`, shows the message, and raises no `pageerror`.
  - **F5:** a file saved before an erase, brought in after it, restores the stars.
- [ ] **Step 2: Run.** Expected: FAIL (no `#progSave`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** everything.
- [ ] **Step 5: Commit:** "She can carry her progress to a new phone as a small file".

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

### Task 7: Android wiring, v8 build, checks, closed testing, PR

**Files:**
- `android-app/package.json`: exact versions of the three plugins.
- `android-app/android/app/build.gradle`: versionCode 8.
- `android-app/build.sh`: run `e2e-progress-safety.py --mode both` first, and stop if it fails.
- `hikmat/public/sw.js`: `CACHE`, one step up.
- `Hikmat Games/CLAUDE.md`: the Android section (plugins, mirror, undo, backup rules, the e2e gate).
- `playstore/`: v8 release notes and `v8-closed-test-check.md`.

- [ ] **Install and build.** Run `npm install --save-exact` for the three plugins, then `npx cap sync android`, then `android-app/build.sh`. Expected: e2e PASS, then the signed AAB.
- [ ] **Check the AAB.**
  - `jarsigner -verify` passes, and the SHA-256 is `2A:6C:…:DC:26`.
  - versionCode is 8, and the manifest's only permission is INTERNET.
  - The `Preferences`, `Share` and `Filesystem` plugin classes are present.
  - `base/assets/public/index.html` contains no `chat.whatsapp`, no `youtube` and no live `ai_ask`.
- [ ] **Check on the emulator.**
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
- [ ] **Open the PRs.** Push and open the PRs: `fix/topbar-long-back-label` → `nightly`, then `safety/step-0`, with the screenshots, test output and android-app diffs. Web: no deploy.

## Not in this step

- Key aliasing for renamed content: rule 10.
- Staged rollout: rule 12, at production.
- Lines for `bodhya.net/data-deletion`:
  - Files saved with Save my progress belong to her, so they have to be deleted from WhatsApp or Files.
  - Start fresh keeps an undo copy for 7 days.
  - Uninstalling, or Clear storage, removes everything at once.

## Open questions

- **Q1.** Should `android-app/` move into bodhya-learn, with no signing material? I recommend yes. Until then, the Android half of this step can't go through a PR.
- **Q2.** Should `"hostname": "localhost"` be pinned in `capacitor.config.json`? It's today's default, so nothing changes. Until you decide, it stays unpinned.
