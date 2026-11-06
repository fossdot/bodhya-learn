# Saved states for the progress-safety harness

`e2e-progress-safety.py` replays every file here through an update. For each one it seeds a fresh
browser, opens the build she has now (git ref `play-v7`), then opens the new build on the same
origin, which is how an update lands on her phone. It relaunches the new build three times, saves
once, then opens the old build again, as if the release had been pulled.

Each file then runs a second time on another fresh phone, marked `direct`: the new build opens the
file itself, with no v7 launch first. That is a phone last opened before v7, which Play updates
straight to the newest build, so only the new build's own account fold runs. The direct run is
judged against what v7 left in the first run.

It fails if any of these happen:

- a `hikmat_state_*` key that was in the file, or that v7 left, goes missing
- a star, gem count or level-test result drops below the file's, or below what v7 left
- a stored field is deleted or changes type, a seen-question id is lost, or an entry in
  `seenTypes`, `videosSeen` or `lastLoc` goes
- a lesson she has played closes
- a setting changes: language, theme, sound, Hindi form, friend or `prefsTouched`
- a relaunch changes anything, in storage or in memory, or her next save changes a number or
  loses a field
- the old build reads back a different total, level or setting, closes a lesson she played, or
  gets less than the full curriculum
- a launch shows a page or console error, or boots without the full curriculum

The checks are I1–I5 and R in `docs/plans/2026-11-03-step-0-progress-safety.md`, Task 1, with the
field, seen-list and settings parts added after review. The harness's docstring lists them exactly.

    /tmp/pw/bin/python e2e-progress-safety.py                 # everything, web and native
    /tmp/pw/bin/python e2e-progress-safety.py --only v7-midway

## Formats

There are two kinds of file, told apart by `kind`.

**Export envelope.** This is the file Save my progress writes. It seeds `hikmat_state_guest`.

```json
{"kind": "bodhya-progress", "v": 1, "app": "net.bodhya.learn",
 "savedAt": "2026-11-02T10:00:00.000Z", "state": {"progress": {}, "coins": 0}}
```

**Storage snapshot.** This is a whole origin's localStorage. Use it for a state that one key can't
describe: an old account, two learners, a full phone. Each value is the raw string, exactly as
`localStorage.getItem()` returns it.

```json
{"kind": "storage-snapshot", "note": "what this phone is",
 "localStorage": {"hikmat_state_guest": "{\"progress\":{},\"coins\":0}"},
 "withCaches": true, "fillToQuota": true}
```

`withCaches` and `fillToQuota` are optional:

- `withCaches` adds the three content caches at real size. `hikmat_courses_cache` and
  `hikmat_testbank_cache` are built from `hikmat/public/curriculum.json` and `testbank.json`.
  `hikmat_structure_cache` is built from the grade bands and subjects in `hikmat/setup_data.py`,
  which is what `get_structure` serves.
- `fillToQuota` then adds `zz_filler_*` keys until `setItem` throws.

## The synthetic fixtures

| File | Kind | What it is |
| --- | --- | --- |
| `v7-midway.json` | envelope | A girl halfway through six tracks of v7. She has played 40 lessons and has 1240 gems. She passed the Level 1 test (85%, two attempts) and has seen-question lists for L1 and L2. Games have been introduced, videos watched, and resume points saved. Her settings are changed: Hindi, dark theme, the girl's Hindi form, and Tara as her friend. |
| `pre-ledger.json` | snapshot | A save from before `seenTypes`, `levelTests`, `testSeen` and `videosSeen` existed. It has 12 lessons of the first six activities, gems, and the old Hindi-gloss toggle still on. |
| `retired-account.json` | snapshot | She was signed in as S-0042 (the boy's Hindi form, Kabir as her friend) when accounts were switched off. It holds `hikmat_current`, her account's 15 lessons (some shared with v7-midway, some not) and a small guest profile. `retireAccounts()` folds the account into the guest profile and leaves `hikmat_state_S-0042` in place. |
| `two-accounts.json` | snapshot | Two learners, A and B, on one phone, with `hikmat_current` naming A. B's progress must stay in `hikmat_state_B`. |
| `full-storage.json` | snapshot | v7-midway with the three content caches at real size, then filler until localStorage is full. |
| `heavy.json` | envelope | Every game of every lesson at 3 stars, 99999 gems, and all five level tests passed. |
| `post-erase.json` | envelope | The v7-midway girl just after Start fresh in v7: her settings and nothing else, with no `lastLoc` or `lastMode`. Neither boot save runs on this phone, so it is where a number changed only in memory would go unseen without the next-save check. |

These files are generated, so don't edit them by hand, and don't add any other file to this
folder: the harness refuses to run one, and `.gitignore` ignores it. To rebuild them from the old
build:

    /tmp/pw/bin/python e2e-progress-safety.py --make-fixtures

Every game id, lesson level and question id comes from that build's own `levelsFor()`,
`lessonLevel()` and test bank, so each one is real. Regenerating gives the same files.

## Adding a tester's file

Saves from real phones are the strongest test there is (rule 12 in `docs/progress-logic.md`). They
go in `tests/saves/private/`, never in this folder. That folder is gitignored.

1. Get a parent's OK before you take a child's file. Tell them and the tester what it is for:
   checking that the update keeps her progress. Record the OK before the file is used: who gave
   it, when, and what it covers (this check only, or an AI session as well). Keep that record
   outside this repo.
2. On a phone with v8 or later, open ☰ → My progress → Save my progress, and send the file. That
   gives an export envelope. v7 has no Save button, so on a v7 phone she notes the numbers on
   My progress first, then updates to v8 and saves. The closed-test check in Task 7 of the plan
   asks testers to do exactly this.
3. To take a storage snapshot, use a debug build on an emulator or a test phone. Open it in
   `chrome://inspect`, then run this in the console:

   ```js
   copy(JSON.stringify({kind: "storage-snapshot", note: "phone, date, what happened",
     localStorage: Object.fromEntries(Object.keys(localStorage)
       .filter(k => k.startsWith("hikmat_state_") || k === "hikmat_current")
       .map(k => {
         let v = localStorage.getItem(k);
         if (k === "hikmat_current") {
           const c = JSON.parse(v) || {};
           v = JSON.stringify({id: c.id, gender: c.gender, mascot: c.mascot});
         }
         return [k, v];
       }))}, null, 1))
   ```

   It copies only what the harness replays: every `hikmat_state_*` key, and `hikmat_current` cut
   down to the id, Hindi form and friend that the account fold reads. Everything else stays on the
   phone: the content caches, and what a phone that was once signed in can still hold, such as her
   name and login token in `hikmat_current`, a per-day usage log (`hikmat_att_<id>`) and a device
   id (`hikmat_device_id`).
   If her phone held the content caches, add `"withCaches": true`, and the harness adds them at
   real size, built from this tree's files. Her own cached curriculum can be older than
   `curriculum.json`, and the rollback check R would then fail for a reason that has nothing to do
   with her progress.
4. Save the file as `tests/saves/private/<phone>-<yyyy-mm-dd>.json`. Name the phone (`tester3`,
   `phoneB`), never the girl, because the file name is printed with every result. Don't edit any
   key or value. Every `.json` file under `private/` runs, in a subfolder too and whatever the case
   of its name. Any other file there fails the run, so no save is ever skipped without a word.
5. Run the harness in a plain terminal. Each file runs as `update:private/<name>`, and the summary
   line says how many tester saves ran:

       /tmp/pw/bin/python e2e-progress-safety.py --only private/

   An AI coding session leaves these files out by itself. The harness sees `CLAUDECODE` or
   `AI_AGENT` in its environment, says how many files it left out, and runs none of them. Pass
   `--private` only where the parent's recorded OK covers an AI session. From any other tool that
   runs on your behalf, pass `--no-private`.

## Care

- This repo is public. A tester's file never goes into a commit, an issue, a PR, a chat or a
  screenshot. `.gitignore` ignores `private/`, any other `.json` in this folder, and any
  `bodhya-progress-*` file anywhere in the repo, which is the name Save my progress gives a file.
- A file holds her stars in each lesson, her gems and level-test results, the questions she has
  seen, the videos she has watched, where she stopped, her language, theme and sound, the Hindi
  form she chose and her friend. Accounts are off, so it holds no name and no phone number. A
  snapshot from a phone that was signed in before accounts were switched off can also hold an old
  student id, in a key name (`hikmat_state_<id>`) and in `hikmat_current`. Treat that id like a
  name.
- For files in `private/`, the harness prints which check failed and where (a field, a lesson
  key), but never her values or counts: no settings, no totals, no number of lessons. An old
  student id prints as `hikmat_state_#1`, and a page error prints only its type, because an error
  can quote the data it failed on. Even so, our working rule is that no beneficiary data goes to
  an AI vendor without recorded consent, so an AI session runs these files only with `--private`
  (step 5).
