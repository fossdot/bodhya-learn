# Bodhya Learn redesign brief

Vishal Arya, 3 Nov 2026. Read this with docs/progress-logic.md before any work.

Bodhya Learn (Android package net.bodhya.learn) is a free offline learning game for girls in Champaran, Class 1 to 10, in Hindi and English. The UI is being redesigned. Two sources define the work:

1. Figma file: https://www.figma.com/design/nZSfrHxWNOojo07J8Z7ZG4 (use the Figma MCP). Pages: 01 Structure and progress, 02 Design system, 03 Game template, 04 Home, 05 Results and checkpoints, 06 Topic, learners and settings.
2. docs/progress-logic.md: the progress model, learner profiles and the data-safety rules.

Students' local progress must never be lost.

## Step 0: progress safety release (v8)

Plan: docs/plans/2026-11-03-step-0-progress-safety.md. No visual change apart from the progress sheet buttons and the Start fresh confirm sheet. Ships through closed testing before anything else.

## Steps after Step 0

One pull request per step, each with screenshots at 360 x 640 in light, dark and Hindi.

1. Tokens: turn the Figma variables (Color with Light and Dark, Size) into CSS variables using the code names shown in Figma, such as var(--color-action-fill). Bundle Baloo 2 and Mukta inside the app; nothing loads from the internet. Text styles become CSS classes.
2. Game template (Figma page 03): one game screen with six zones and eight answer modes, used by all 29 games. Pick-one games answer on tap; Build, Order and Fields use Check. A wrong answer gets a cross and a hint that never states the answer. No stars, gems, menu or language buttons during a game.
3. Progress model: five levels inside each topic, with checkpoints of 10 questions, a 75% pass mark and no clock. If she leaves mid-checkpoint, that question is swapped when she returns. Use the four-condition open rule from docs/progress-logic.md, add only the new fields it names, and never rewrite old ones. Gems stay stored and counted but are not shown.
4. Screens: Home (page 04), then the results and checkpoint screens (page 05), then the topic path, level sheet and Me screen (page 06).
5. Learners: private sign-in. Never show a list of learners, their faces or how many there are. A phone with one learner opens Home directly. With two or more, every launch asks her name; a match shows only that learner's face, and a new name picks a face. Use hikmat_profiles and hikmat_profile_active, never hikmat_current. Erase works per learner, keeps a 7-day undo, and never names anyone else.

## Rules for every step

- Never delete, rename or reshape a stored key or field. Never change the package name, the signing key, or the Capacitor scheme and hostname.
- Never rename course, lesson or game type keys.
- Every string exists in English and Hindi. Hindi follows her chosen gender form, or uses a neutral form. Check Hindi layouts at 360 x 640.
- Tap targets are at least 48dp. Text contrast meets WCAG AA in both themes.
- Pictures, icons and guide faces in Figma are placeholders. Keep the current ones until replacements arrive.
- If Figma and docs/progress-logic.md disagree, the doc wins on logic and Figma wins on layout. When unsure, ask instead of guessing.
