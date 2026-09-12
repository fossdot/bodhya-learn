# Copyright (c) 2026, FOSS United and contributors
# For license information, please see license.txt
"""One rung of the L1–L5 ladder: when its test falls due and how it is scored.

Two students draw independent random n-subsets from a bank of M questions, so a question on
one paper appears on the other with probability n/M. Keeping M >= 10*n holds the expected
overlap at <= ~10% — the bank check is a warning here (the pool is per learner, not per level,
so Desk cannot know the exact denominator), never a hard error.
"""
import frappe
from frappe.model.document import Document

OVERLAP_FACTOR = 10


class TestLevel(Document):
	def validate(self):
		lv = int(self.level or 0)
		if not (1 <= lv <= 5):
			frappe.throw("Level must be between 1 and 5.")
		n = int(self.questions_per_paper or 0)
		if n < 1:
			frappe.throw("Questions per paper must be at least 1.")
		if not (1 <= int(self.pass_pct or 0) <= 100):
			frappe.throw("Pass mark must be between 1 and 100.")
		if int(self.stars_required or 0) < 1:
			frappe.throw("Stars to unlock the test must be at least 1.")
		for f in ("easy_secs", "medium_secs", "hard_secs"):
			if int(self.get(f) or 0) < 5:
				frappe.throw("Every per-question timer must be at least 5 seconds.")
		if frappe.db.exists("DocType", "Test Question"):
			bank = frappe.db.count("Test Question", {"level": lv, "active": 1})
			if bank < OVERLAP_FACTOR * n:
				frappe.msgprint(
					f"Level {lv} has {bank} active questions for {n}-question papers. With fewer than "
					f"{OVERLAP_FACTOR * n}, two learners' papers may share more than 10% of questions.",
					title="Small question bank", indicator="orange")
