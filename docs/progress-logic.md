# Bodhya Learn: progress logic and data safety

Vishal Arya. Updated 3 Nov 2026. Source of truth: the Claude Doc of the same name.

## What this covers

Progress moves from one app-wide level to five levels inside each topic, and no student loses a star, a lesson or a setting on the way.

Everything here is read from the public repo [fossdot/bodhya-learn](https://github.com/fossdot/bodhya-learn) (nightly branch, last commit 15 Sep 2026). The Play build v7 from 2 Nov 2026 may be newer, so check every key named here against it before building.

Whether pooling stars across subjects was meant to push girls into other subjects is not known. Nothing below depends on it.

## How progress works today

All of a student's progress sits in one localStorage key, `hikmat_state_guest`, inside the app's WebView. Accounts are off in the Play build, so each phone holds exactly one profile.

| Field | What it holds | Notes |
| --- | --- | --- |
| `progress[course][lesson][game]` | Stars, 0 to 3 | `game` is the game type id, such as `learn` or `listen` |
| `coins` | Gems | +5 per right answer, +10 per star; nothing spends them |
| `levelTests["1"]` to `["5"]` | `passed`, `bestPct`, `attempts` | One ladder for the whole app |
| `testSeen["L1"]` to `["L5"]` | Question ids already served | Stops a retake repeating a paper |
| `seenTypes`, `videosSeen` | Games already introduced, videos watched | |
| `lastLoc`, `lastMode` | Resume point per door, last door used | |
| `lang`, `sound`, `theme`, `gender`, `mascot`, `prefsTouched` | Her settings | Kept by Start fresh |

v8 (Step 0) adds two fields to this state, `eraseGen` and `nativeCopy`, and keeps more beside it. All of it is listed under "Carrying every student over", with what step 3 has to do with it.

A lesson's level is its position in its track (`defaultLevel`), not its difficulty. None of the 283 lessons has a level set by hand.

Her level is the first of L1 to L5 whose test she hasn't passed, counted across every track. Stars from that level's lessons in any track fill one 100-star bar, and at 100 the test is offered.

Nothing locks: `lessonLock()` returns null, by a deliberate earlier fix. The screens still say otherwise. The level sheet shows padlocks, and the pass screen says new lessons have opened.

Some protection already exists. `mergeSavedState` keeps the higher value of every number. `retireAccounts` kept the old account keys when accounts were switched off. Start fresh asks twice and keeps her settings.

## Where data can be lost today

Six gaps, each fixed by a rule in the section on keeping data safe.

- **Unguarded save.** `save()` has no try/catch. If storage is full, the write throws and that game's stars are never kept.
- **Shared space.** The course cache and the test bank sit in the same localStorage as her progress. The bundled files are about 1.2 MB and 0.9 MB. Caches can be rebuilt; progress can't.
- **Double tap on Start fresh.** The confirm button appears exactly where the first tap landed, so a double tap erases everything. There is no undo.
- **App identity.** Her data is visible only to the origin `https://localhost` inside `net.bodhya.learn`. A new Capacitor scheme or hostname hides it; a new package name or signing key means a fresh install.
- **No second copy.** Uninstalling, Clear storage or a new phone loses everything. Nothing exports it.
- **Key renames.** Stars hang on the course key, lesson key and game type. Renaming any of them in Desk orphans those stars.

Shared phones add a seventh problem that is mixing, not loss. A sister's play lands in the same profile, and Start fresh is the only way to separate them.

## The new model

Each topic has five levels of its own lessons, and a short checkpoint opens the next level of that topic only.

Content sits in one chain: Subject (or Life Skills) → Class → Topic → Level 1 to 5 → Lesson → Game. The words on screen follow it; "door", "track", "trail", "map" and "stall" go.

In Hindi: Subject विषय, Class कक्षा, Topic अध्याय, Level स्तर, Lesson पाठ, Game खेल, Checkpoint चुनौती, Home होम.

- **Levels.** Lessons keep their current level numbers (`lessonLevel`), so no content or stored data changes. A 10-lesson topic has two lessons per level.
- **Checkpoint.** One per level per topic: 10 questions from that level's lessons in that topic only, fewer if the pool is smaller (minimum 5, as today). Pass mark 75%. No clock.
- **Unlock.** Level n+1 of a topic opens when she passes that topic's level-n checkpoint. Level 1 of every topic is always open.
- **Jump ahead.** Any checkpoint whose level is open can be taken at any time. Taken before she has played that level, its questions come from all of its lessons, so an older girl can skip what she knows.
- **Never closed.** A lesson she has played stays open forever, whatever the checkpoints say. She always has something to play.
- **Leaving mid-checkpoint.** When she comes back, the question she left is swapped for a new one. Nothing is voided.
- **Stars.** Stored exactly as now, 0 to 3 per game. The lessons count keeps today's meaning (any star), so a number she has seen never drops.
- **Gems.** Gone from every screen. Still stored and still counted, so nothing is lost if they ever return.
- **The app-wide level.** Gone from the screen with its pill and sheet. `levelTests` and `testSeen` stay in storage untouched. Her old results show when she taps her current level on a topic's path, for example "Level 1 test passed, 85%".

### Profiles for shared phones

A new phone first asks "Who's learning?": type her name, no login. No learner ever sees another: the app never lists names, faces or how many learners use the phone.

- The existing `hikmat_state_guest` becomes the first profile, in place, with nothing copied or moved.
- New profiles are stored as `hikmat_state_p_<id>`, listed in `hikmat_profiles`. The active one is named in `hikmat_profile_active`. The list is used only for lookup and is never shown.
- **Sign-in.** She types her name. A match shows only that learner's face to confirm. No match, or "No, I'm new", starts a new learner who picks a face; a taken name gets a letter added, like Rekha K.
- **Matching.** Names match after trimming spaces and ignoring case. Hindi names match as typed.
- **When it asks.** A phone with one learner opens Home directly. With two or more, every launch asks the name, and Switch in Me signs her out.
- Never point `hikmat_current` at a profile. With accounts off, `retireAccounts()` merges whatever it names into the guest profile on the next launch, folding one sister's stars into another's.
- Friend, Hindi form, language and sound belong to each profile. A new profile starts with the phone's current choices.
- Start fresh becomes "Erase this learner's progress" inside that learner's settings. Other learners are untouched, and the confirm screen never names them.

## Carrying every student over

There is no migration step. The new rules read the data that already exists and add only the new data named below, so nothing is converted and nothing can fail halfway.

A level of a topic is open if any one of these is true:

1. It is level 1.
2. She passed that topic's checkpoint for the level below (new field).
3. She has stars in any lesson of that topic at this level or higher, so she got there before.
4. She passed the old app-wide test for the level below (`levelTests[n-1].passed`), because the old pass screen told her those lessons had opened.

A lesson is open if its level is open or she has any star in it.

The only new data in this step is `checkpoints[course][n] = {passed, bestPct, attempts}`, `testSeen["C:<course>:<n>"]` for its question ids, and `schema: 2`. No old field is rewritten.

**What Step 0 (v8) already added.** It stays as it is (rule 1), and this step keeps every item working:

- **`eraseGen`**, in her state, on the web and in the app. It orders her erases; it does not count them. Start fresh adds 1. Bring back what I erased, and a launch that adopts an erase made in v7 (below), set it one above both copies. Missing, or anything but a positive number, means 0. Bring my progress never takes a file's.
- **`nativeCopy: true`**, in her state, on every write of it in the Android app (`save()` sets it, and so does the account fold, which writes directly), never on the web. v7 carries it along on its own saves, but v7's Start fresh drops it, as it drops `eraseGen`.
- **`hikmat_native_saved`**, a localStorage key holding `"1"` (app only). The first write of her state that localStorage keeps sets it, and nothing removes it, so it means: this WebView storage has held a marked state since its data was last wiped.
- **`hikmat_native_unmerged`**, a localStorage key holding a state key such as `hikmat_state_guest` (app only). A launch began without a readable state under that key (after an install that restored nothing, or a wipe), and since then no launch has had localStorage keep the save it makes right after reading the native copy. Such a launch sets it, and normally removes it itself, once the native copy has been read and localStorage has kept the save after it.
- **The launch marks**, taken by the first statements of the game's script, before anything can write: `NATIVE_APP` (`window.Capacitor` is there), `BOOT_STATE` (which `hikmat_state_*` keys held a state that parses), `BOOT_MARKED` (which of those carry `nativeCopy`), and `BOOT_NATIVE_SAVED` and `BOOT_UNMERGED` (the two keys above). They live in memory and must stay first. From them comes `localWasMissing`: her state was missing at boot, or `hikmat_native_unmerged` names it. Separate from them is the mirror gate: nothing is written to the native copy until the launch has read it, and nothing at all in a launch whose read failed twice or that found no plugin.
- **The undo copy**, `hikmat_undo_<id>` (`hikmat_undo_guest` with accounts off), in localStorage and, in the app, in the native copy: `{kind: "bodhya-erase-undo", v: 1, erasedAt, until, state}`, plus `fold: {id, gender, mascot}` when an account fold was waiting. Start fresh writes it (one store is enough) with a live earlier copy merged in, so the 7 days run from the newest erase. A launch that adopts an erase made in v7 writes one too. It goes after a kept Bring back what I erased, or when the app reads it after `until` (at launch, on opening My progress, or at Start fresh). So on a phone where the app is not opened, it stays past the 7 days, and in the backups taken meanwhile.
- **The native copy**, in Capacitor Preferences (`shared_prefs/CapacitorStorage.xml`, which the Auto Backup and device-transfer rules include): `hikmat_state_guest`, the JSON of her latest save, and `hikmat_undo_guest`. It is written only once the launch has read it, the latest save winning, so it can be ahead of localStorage (a save localStorage refused) or behind it (a launch that could not read it). Nothing removes `hikmat_state_guest`; Start fresh overwrites it.
- **The progress file** from Save my progress, `bodhya-progress-<YYYY-MM-DD>.json`: `{kind: "bodhya-progress", v: 1, app, savedAt, state}`, with her whole state. In the app it is written to the app's cache folder first, one file per day, and nothing deletes it yet (step 5 will). v8 reads files of any `v` from 1, up to 2 MB, and keeps only the fields `cleanImportedState` knows.
- **The content caches** (`hikmat_courses_cache`, `hikmat_structure_cache`, `hikmat_testbank_cache`), in IndexedDB (database `hikmat_content`, store `kv`), or still in localStorage where IndexedDB refuses them. When a cache is missing, the bundled files (lessons, test bank) and the game's own defaults (grade structure) stand in, and only a server answer writes a cache again. No progress goes there.

At launch, before any `eraseGen` is compared, v8 looks for an erase made in v7: her state at boot lacked `nativeCopy` (`BOOT_MARKED`), `hikmat_native_saved` is set, the native copy carries `nativeCopy`, and `localWasMissing` is false. Then localStorage stands whatever the two `eraseGen`s, `eraseGen` goes one above both, and the native copy (if it holds progress) becomes the undo copy, with any live earlier undo copy merged in, for 7 days from that launch (`adoptOlderErase`). If neither store takes that undo copy, the usual merge runs instead. The usual merge: the copy with the higher `eraseGen` wins outright, and equal ones merge, with the native copy as the base when `localWasMissing`.

**What this step has to do with them:**

- **Every merge of two states goes through `mergeSavedState`, which copies only the fields it names:** the launch merge, the account fold, Bring my progress, Bring back what I erased, and the merge of two undo copies (`withEarlierUndo`, used by a second Start fresh within the 7 days and by `adoptOlderErase`). Bring also keeps only what `cleanImportedState` names. So `checkpoints` goes into both: merged like `levelTests` (passed if either copy passed, the higher `bestPct`, attempts the higher for one learner and added up for two), and cleaned with `_safeKeys` at both levels. Otherwise a pass is lost wherever a copy that holds it, or a newer value of it, meets one that doesn't: a native copy ahead of localStorage, a launch after a wiped WebView that could not read the native copy (its localStorage is then the side merged in), an undo, a second erase, an adopted v7 erase, a brought file. `testSeen["C:<course>:<n>"]` needs nothing, because both functions take any `testSeen` key.
- **The tests must see checkpoints.** In `e2e-progress-safety.py`, `guarded()` collects only stars, gems and level tests, and `lowered()` compares only those, so no test would notice a lost pass today. Add `checkpoints` to `guarded()` and seed passes in the fixtures; then every check built on `lowered()` covers them. The rollback checks (R in `judge()`, U8, R2 a) and N8 compare totals and open lessons instead, so they need a checkpoint comparison of their own, such as the levels open per topic.
- **`schema` needs a rule, and something that reads it.** Ordinary saves in v7 and v8 carry it; Start fresh (in either) and a merge whose base lacks it drop it. For example, `save()` writes it on every save, as it writes `nativeCopy`, and a state without it is schema 1.
- **A checkpoint in progress** ("Leaving mid-checkpoint", above) has no place yet. If it is kept in her saved state, it is new data too, and it needs a rule in the merges, the undo copy and the file (most likely: never merged, never brought).
- **New defaults go into `withBootDefaults()`** as well as `fresh()` and the boot section, because the launch merge puts its state in place after the boot section has run.
- **`hasProgress()` counts a passed checkpoint**, as it counts a passed level test. Otherwise checkpoints passed without a star get no undo copy when a launch adopts an erase made by an older build, and Bring my progress takes a file's settings over hers.
- **New code that writes a state key goes through `save()`, or sets `nativeCopy` itself.** At the next launch, an unmarked state next to a marked native copy reads as an erase made in v7.
- **If this step is pulled back to v8,** v8 keeps `checkpoints` through its own saves but drops them in the merges above. The rollback test runs Start fresh then Bring back what I erased, and Bring my progress, on v8, and checks which levels are open when this step returns.
- **Narrow loss paths in v8** found after it was built are listed in Step 0's plan ("Found 6 Nov"), with what became of them. Where this step touches the same code, it keeps their fixes, or fixes them.

On the first launch after the update, every lesson she played is open, every level she reached is open, and every number she saw is the same or higher. If a release has to be pulled, the previous build still reads the same data, because nothing it relies on changed.

## Rules that keep data safe

Every release follows these twelve rules; the build should fail if a test catches one broken.

1. **Add, never reshape.** Never delete, rename or change the shape of an existing key or field. New things get new fields, and old ones stay readable.
2. **Numbers only go up.** Any merge, import or restore keeps the higher value, as `mergeSavedState` already does.
3. **Nothing earned closes.** A lesson with a star, a level she reached and a test she passed stay open.
4. **Guard every write.** Wrap `save()` in try/catch. If storage is full, drop the content caches, which rebuild from the bundled files, and retry. Never drop progress to make room.
5. **Give progress its own space.** Move the course, structure and test bank caches out of localStorage into IndexedDB or the Cache API.
6. **Keep a second copy outside the WebView.** Mirror each save to native storage (Capacitor Preferences). At launch, merge that copy back with the higher-value rule, which repairs a WebView that comes back empty.
7. **Let her carry it.** "Save my progress" writes a small file through the Android share sheet (WhatsApp, Bluetooth, Files). "Bring my progress" reads one and merges it. This covers uninstalls and new phones, offline.
8. **Keep Android Auto Backup on** and include the native copy in its rules. It restores on reinstall where Google backup is switched on; treat it as a bonus, not the plan.
9. **Never change the app's identity.** Keep the package `net.bodhya.learn`, the signing key, and the Capacitor scheme `https` with hostname `localhost`.
10. **Never rename published keys.** Course keys, lesson keys and game type ids stay fixed. If one must change, ship an alias map (old to new) applied at load with the higher-value rule.
11. **Make erasing hard by accident.** Confirm on a separate sheet, with the erase button in a different place. Keep an undo copy for 7 days. "Delete my data" stays a full, immediate delete.
12. **Test on real saves.** Keep exported states from a few tester phones, with permission, and run them through the e2e scripts. Fail the build if any total drops or any played lesson closes, then release through Play's staged rollout.

## Decisions

Decided by Vishal on 3 Nov 2026.

- [x] **Checkpoint clock:** none. Quick Fire keeps its own clock, since speed is that game's point.
- [x] **Gems:** dropped from every screen. `coins` stays in storage and keeps counting (rule 1).
- [x] **Profiles:** she types her name and picks a face, and no learner ever sees who else uses the phone.
- [x] **Old Level 1 to 5 results:** shown when she taps her current level on a topic's path, for example "Level 1 test passed, 85%".
- [x] **Hindi words:** Topic अध्याय, Level स्तर, Checkpoint चुनौती, Home होम. अध्याय is the chapter word from school textbooks, and स्तर is already in the app. चुनौती keeps the checkpoint part of the game: परीक्षा brings exam pressure, and जाँच would clash with the जाँचो (Check) button. घर is already the At Home place, so Home stays होम.
- [x] **Keys against v7:** all found in the 15 Sep code, and the new keys are free. The final check runs in the v7 code, with the script below.

## Check before building

The Play build v7 is newer than the public repo, which stops at 15 Sep. So every key above gets checked in the code that builds v7 before any change lands. Run this from that repo's root; Claude Code can run it as is.

```bash
#!/usr/bin/env bash
# Checks the keys and settings named in "Bodhya Learn: progress logic and data safety"
# against the code that builds the Play release. Run from that repo's root.
cd "${1:-.}" || exit 1
EX="--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=build --exclude-dir=dist"
CODE="--include=*.html --include=*.js --include=*.mjs --include=*.ts"
where() { grep -rIlF $EX $CODE -- "$1" . 2>/dev/null | head -1; }

echo "== Must exist: the data rules rely on these"
for k in 'hikmat_state_' 'hikmat_current' 'hikmat_courses_cache' 'hikmat_structure_cache' \
         'hikmat_testbank_cache' 'function retireAccounts' 'function mergeSavedState' \
         'function lessonLock' 'function defaultLevel' 'levelTests' 'testSeen' 'seenTypes' \
         'videosSeen' 'lastLoc' 'lastMode' 'prefsTouched' 'AUTH_ENABLED'; do
  f=$(where "$k"); if [ -n "$f" ]; then echo "found    $k  ($f)"; else echo "MISSING  $k"; fi
done

echo "== Must be free: the new keys must not collide"
for k in 'hikmat_profiles' 'hikmat_profile_active' 'checkpoints' 'state.schema'; do
  f=$(where "$k"); if [ -z "$f" ]; then echo "free     $k"; else echo "TAKEN    $k  ($f)"; fi
done

echo "== App identity: must never change"
grep -rIhE $EX 'appId|androidScheme|hostname' --include='capacitor.config.*' . 2>/dev/null \
  || echo "capacitor.config not in this repo: check it in the Android project"
grep -rIhE $EX 'applicationId' --include='build.gradle*' . 2>/dev/null \
  || echo "build.gradle not in this repo: check it in the Android project"
```

Every line under "Must exist" should say found, and every line under "Must be free" should say free. A MISSING or TAKEN line means v7 changed something: update this doc first, then build. The identity lines should show `net.bodhya.learn`, scheme `https` and hostname `localhost`.
