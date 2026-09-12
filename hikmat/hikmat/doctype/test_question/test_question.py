# Copyright (c) 2026, FOSS United and contributors
# For license information, please see license.txt
"""One question in the level-test bank.

A level test is drawn per learner from the questions of the LESSONS SHE HAS PLAYED at her
current level, so every row must name its lesson; the level rides along from there. The
validation mirrors the old Module Test: a question that cannot be answered (answer not among
its choices) must never reach a scored, no-retry screen.
"""
import frappe
from frappe.model.document import Document


def split_choices(s):
	return [c.strip() for c in (s or "").splitlines() if c.strip()]


class TestQuestion(Document):
	def validate(self):
		choices = split_choices(self.choices)
		if len(choices) < 2:
			frappe.throw("A question needs at least 2 choices (one per line).")
		if len(set(choices)) != len(choices):
			frappe.throw("Choices must be different from each other.")
		if (self.answer or "").strip() not in choices:
			frappe.throw("The answer must be exactly one of the choices.")
		self.answer = self.answer.strip()
		self.choices = "\n".join(choices)
		if self.difficulty not in ("Easy", "Medium", "Hard"):
			self.difficulty = "Medium"
		# fetch_from only fills on the client; make the denormalised copies authoritative here
		if self.lesson:
			row = frappe.db.get_value("Lesson", self.lesson, ["track", "lesson_key", "level"], as_dict=True)
			if row:
				self.track_key, self.lesson_key, self.level = row.track, row.lesson_key, int(row.level or 1)
