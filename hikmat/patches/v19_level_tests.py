# Copyright (c) 2026, FOSS United and contributors
# For license information, please see license.txt
"""Level tests (L1–L5) replace the per-track Module Tests.

WHAT CHANGES. Every lesson now carries a level 1–5 (position-in-track by default: the first
fifth of each track is Level 1, the last fifth Level 5 — see api.default_level; Desk can set
any lesson's level by hand). A learner sees lessons up to her current level. Once she has
earned the level's star quota (100 by default, on Test Level) new lessons at that level PAUSE
until she passes the level test: 20 questions drawn from the lessons SHE has played at that
level, one countdown per question by difficulty, 75% to pass, voided if she leaves the screen.
Passing opens the next level. Replays are never blocked.

WHAT THIS PATCH DOES, in order:
  1. seed_content()  — reseeds Track/Lesson/Dialogue from data/curriculum.json, now WITH the
     level field. Student data is UNTOUCHED (attempts/events reference lessons by stable string
     keys; recreated docs keep the same names). Also creates the three unpublished
     "education stage" Grade Bands (11-12 / ug / pg) the sign-up class picker now offers.
  2. seed_test_levels() — the five Test Level rows with default rules (skips existing rows).
  3. Carries every Module Test Question over into the Test Question bank (source Migrated),
     attached to its track's FIRST lesson, difficulty Medium — the only human-authored test
     content on the site (27 rows on prod), not to be lost. Prints the count.
  4. Drops the Module Test and Module Test Question DocTypes AND their tables. Their Test
     Attempt rows stay (level empty, track set) and still show in Desk and in
     get_progress.tests. The doctype folders are deleted from the app in this same commit —
     without that, sync_all() on the next migrate would recreate both from the JSON on disk
     and reattach the surviving tables (see _drop_doctype).
  5. Rebuilds the Desk workspace, busts every cached read payload.

Idempotent / re-runnable: step 3 is guarded on its own output (a Test Question with
source=Migrated), so a re-run after a partial failure cannot double the carried-over bank;
steps 1, 2 and 5 are each independently re-runnable, and step 4 tolerates an absent doctype.
"""
import frappe

from hikmat import setup_data


def _drop_doctype(name):
    """Retire a DocType for good. Two things are needed and neither is enough on its own.

    The DocType's JSON folder has to be gone from the app — removed in this same commit. While
    it is on disk, the next `bench migrate` re-imports it during sync_all() (the "has it
    changed?" guard is skipped once the tabDocType row is missing) and hands it straight back
    its old table, fully populated, in the facilitator's Desk.

    And the table has to be dropped by hand: frappe.delete_doc("DocType", ...) removes only the
    tabDocType / DocField / DocPerm metadata and never the physical table — frappe's own test
    suite says so ("delete_doc doesnt drop tables").
    """
    if not frappe.db.exists("DocType", name):
        print("=== v19: %s already absent ===" % name)
    else:
        try:
            n = frappe.db.count(name)
        except Exception:
            n = "?"
        print("=== v19: deleting DocType %s (%s rows) ===" % (name, n))
        frappe.delete_doc("DocType", name, force=True, ignore_missing=True, delete_permanently=True)
    # Safe to drop the data: _migrate_module_tests() ran first and copied every question into
    # Test Question, and it refuses to proceed at all if that bank doctype is missing.
    try:
        frappe.db.sql_ddl("DROP TABLE IF EXISTS `tab%s`" % name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "v19 drop table %s" % name)


def _migrate_module_tests():
    if not frappe.db.exists("DocType", "Module Test") or not frappe.db.exists("DocType", "Test Question"):
        return
    # Guard on our own OUTPUT, not on the source rows. Dropping the doctype used to be the only
    # thing that made a second run a no-op, so anything that interrupted this patch between the
    # commit below and that drop — or a hand re-run — inserted a second copy of all 27 questions.
    # Test Question is autoname:hash with no unique field, so nothing else would have caught it.
    if frappe.db.exists("Test Question", {"source": "Migrated"}):
        print("=== v19: Module Test questions already carried over ===")
        return
    moved = skipped = 0
    for mt in frappe.get_all("Module Test", fields=["name", "track"]):
        lesson = frappe.db.get_value("Lesson", {"track": mt.track}, "name", order_by="sort_order asc, creation asc")
        qs = frappe.get_all("Module Test Question", filters={"parent": mt.name},
                            fields=["question", "question_hi", "emoji", "choices", "answer", "teach", "teach_hi"],
                            order_by="idx asc")
        if not lesson:
            skipped += len(qs)
            continue
        for q in qs:
            try:
                frappe.get_doc({
                    "doctype": "Test Question", "lesson": lesson, "source": "Migrated", "active": 1,
                    "difficulty": "Medium",
                    "question": q.question, "question_hi": q.question_hi or "", "emoji": q.emoji or "",
                    "choices": q.choices, "answer": q.answer, "teach": q.teach or "", "teach_hi": q.teach_hi or "",
                }).insert(ignore_permissions=True)
                moved += 1
            except Exception:
                skipped += 1
    frappe.db.commit()
    print("=== v19: carried %d Module Test question(s) into the level bank, %d skipped ===" % (moved, skipped))


def execute():
    setup_data.seed_content()
    setup_data.seed_test_levels()
    _migrate_module_tests()

    # Parent first: its Table field points at the child, so dropping the child first would
    # leave a dangling table field behind.
    _drop_doctype("Module Test")
    _drop_doctype("Module Test Question")

    try:
        # Rebuild the whole workspace rather than hand-editing the shortcuts child table. The
        # Desk page renders from Workspace.content, not from `shortcuts`, so patching the child
        # table alone left the retired Module Test tile on screen and never drew the new ones.
        # setup_workspace() writes both, and its list already carries Test Questions /
        # Test Levels / Test Attempts and no Module Test.
        setup_data.setup_workspace()
        print("=== v19: Desk workspace rebuilt ===")
    except frappe.DoesNotExistError:
        pass
    except Exception:
        frappe.log_error(frappe.get_traceback(), "v19 workspace")

    try:
        from hikmat.api import clear_content_cache
        clear_content_cache()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "v19 cache bust")
    frappe.clear_cache()
    frappe.db.commit()
    print("=== v19: level tests live; module tests retired ===")
