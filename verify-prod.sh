#!/usr/bin/env bash
# Is what's on prod what we think is on prod? Read-only GETs against public endpoints —
# no SSH, no dashboard. Run it before a deploy to see the "before", and after to confirm.
#   ./verify-prod.sh                      # defaults to learn.bodhya.net
#   ./verify-prod.sh https://other.site
set -uo pipefail
SITE="${1:-https://learn.bodhya.net}"
EXPECT_BANK="${EXPECT_BANK:-2780}"      # the seeded bank. A site that had Module Test ROWS
                                        # authored in Desk carries those over on top (the dev
                                        # site had 27); prod had none, so 2780 is the floor.
EXPECT_LESSONS="${EXPECT_LESSONS:-283}"
ok=0; bad=0
say(){ if [ "$1" = pass ]; then ok=$((ok+1)); printf '  \033[32mPASS\033[0m %s\n' "$2"; else bad=$((bad+1)); printf '  \033[31mFAIL\033[0m %s\n' "$2"; fi; }

echo "== $SITE =="

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$SITE/api/method/hikmat.api.get_settings")
[ "$code" = 200 ] && say pass "site is up (get_settings $code)" || say fail "site is up (get_settings $code)"

# An undeployed site answers this with HTTP 417 and a JSON *error* body, which still parses —
# so check for the exception/missing "message" explicitly rather than trusting json.load().
read -r bank levels < <(curl -s --max-time 25 "$SITE/api/method/hikmat.api.get_test_bank" | python3 -c '
import json,sys
try:
    r = json.load(sys.stdin)
    if "exception" in r or "exc_type" in r or "message" not in r:
        print(-1, -1)
    else:
        d = r.get("message") or {}
        print(len(d.get("bank") or []), len(d.get("levels") or []))
except Exception:
    print(-1, -1)')
if [ "$bank" = "-1" ]; then say fail "get_test_bank exists (missing → the new code is NOT deployed yet)"
else
  say pass "get_test_bank responded"
  [ "$bank" -ge "$EXPECT_BANK" ] && say pass "bank has $bank rows (>= $EXPECT_BANK)" || say fail "bank has $bank rows, expected >= $EXPECT_BANK"
  [ "$levels" = 5 ] && say pass "5 Test Levels" || say fail "$levels Test Levels, expected 5"
fi

# Assets are served "cache-control: immutable, max-age=31536000" under a PLAIN filename, so an
# intermediary will hand back the previous deploy's copy for a year. Always cache-bust here,
# or a perfectly good deploy reads as a failed one.
CB="cb=$(date +%s)$$"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$SITE/assets/hikmat/testbank.json?$CB")
[ "$code" = 200 ] && say pass "testbank.json served ($code)" || say fail "testbank.json served ($code, expected 200)"

read -r total leveled < <(curl -s --max-time 25 "$SITE/assets/hikmat/curriculum.json?$CB" | python3 -c '
import json,sys
try:
    ls = [l for t in json.load(sys.stdin) for l in t["lessons"]]
    print(len(ls), sum(1 for l in ls if l.get("level")))
except Exception:
    print(0, 0)')
[ "$total" -ge "$EXPECT_LESSONS" ] && say pass "curriculum has $total lessons" || say fail "curriculum has $total lessons, expected >= $EXPECT_LESSONS"
[ "$total" -gt 0 ] && [ "$leveled" = "$total" ] && say pass "every lesson carries a level ($leveled/$total)" || say fail "lessons with a level: $leveled/$total"

live=$(curl -s --max-time 25 "$SITE/api/method/hikmat.api.get_courses" | python3 -c '
import json,sys
try:
    c = json.load(sys.stdin)["message"]; ls=[l for t in c for l in t["lessons"]]
    print(sum(1 for l in ls if l.get("level")), len(ls))
except Exception: print(0,0)')
set -- $live
[ "$1" = "$2" ] && [ "$1" != 0 ] && say pass "live get_courses: every lesson carries a level ($1/$2)" || say fail "live get_courses levels: $1/$2"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$SITE/assets/hikmat/game.html?$CB")
[ "$code" = 200 ] && say pass "game.html served ($code)" || say fail "game.html served ($code)"

echo
echo "  $ok passed, $bad failed"
[ "$bad" -eq 0 ] || exit 1
