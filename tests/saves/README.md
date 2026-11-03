# Saved states for the progress-safety harness

`e2e-progress-safety.py` replays every file here through an update. For each one it seeds a fresh
browser, opens the build she has now (git ref `play-v7`), then opens the new build on the same
origin, which is how an update lands on her phone. It relaunches the new build three times, then
opens the old build again, as if the release had been pulled. It fails if any of these happen:

- a star, gem count or level-test result drops
- a lesson she has played closes
- a setting changes
- a relaunch changes anything
- the old build can no longer read what the new one left

The checks are I1–I5 and R in `docs/plans/2026-11-03-step-0-progress-safety.md`, Task 1.

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

These files are generated, so don't edit them by hand. To rebuild them from the old build:

    /tmp/pw/bin/python e2e-progress-safety.py --make-fixtures

Every game id, lesson level and question id comes from that build's own `levelsFor()`,
`lessonLevel()` and test bank, so each one is real. Regenerating gives the same files.

## Adding a tester's file

Saves from real phones are the strongest test there is (rule 12 in `docs/progress-logic.md`). They
go in `tests/saves/private/`, never in this folder. That folder is gitignored.

1. Get a parent's OK before you take a child's file. Tell them and the tester what it is for:
   checking that the update keeps her progress.
2. On a phone with v8 or later, open ☰ → My progress → Save my progress, and send the file. That
   gives an export envelope. v7 has no Save button, so on a v7 phone she notes the numbers on
   My progress first, then updates to v8 and saves. The closed-test check in Task 7 of the plan
   asks testers to do exactly this.
3. To take a storage snapshot, use a debug build on an emulator or a test phone. Open it in
   `chrome://inspect`, then run this in the console:

   ```js
   copy(JSON.stringify({kind: "storage-snapshot", note: "phone, date, what happened",
     localStorage: Object.fromEntries(Object.keys(localStorage).map(k => [k, localStorage.getItem(k)]))}, null, 1))
   ```

   Delete the three `*_cache` keys and set `"withCaches": true` instead. A phone's cached
   curriculum can be older than `curriculum.json`, and the rollback check R would then fail for a
   reason that has nothing to do with her progress.
4. Save the file as `tests/saves/private/<phone>-<yyyy-mm-dd>.json`. Don't edit any key or value.
5. Run the harness. Each file runs as `update:private/<name>`:

       /tmp/pw/bin/python e2e-progress-safety.py --only private/

## Care

- This repo is public. A tester's file never goes into a commit, an issue, a PR, a chat or a
  screenshot.
- A file holds her stars in each lesson, her gems and level-test results, the questions she has
  seen, the videos she has watched, where she stopped, her language, theme and sound, the Hindi
  form she chose and her friend. Accounts are off, so it holds no name and no phone number. A
  snapshot from a phone that was signed in before accounts were switched off can also hold an old
  student id inside a key name (`hikmat_state_<id>`). Treat that id like a name.
- For files in `private/`, the harness prints which check failed and where, but never her values:
  no settings, no totals. Even so, our working rule is that no beneficiary data goes to an AI
  vendor without recorded consent. So run private files in a plain terminal, not in an AI
  session, unless the parent's OK covers that.
