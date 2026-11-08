#!/bin/bash
# Rebuild a signed release .aab from the current ../index.html (run ../sync-game.sh first).
# Usage:  ./build.sh        (bump versionCode in android/app/build.gradle first for a new release)
#         E2E_PYTHON=/path/to/python ./build.sh   (a Python with Playwright; default ~/.venvs/bodhya-e2e/bin/python)
set -uo pipefail
cd "$(dirname "$0")"

export JAVA_HOME="${JAVA_HOME:-$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$(brew --prefix)/share/android-commandlinetools}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

# Signing material never enters this repo (it is public). keystore.properties points at the upload
# keystore, which lives outside git. Without it Gradle quietly builds an UNSIGNED release.
# It is read where it lives: android/keystore.properties if one is there, else the copy beside the
# upload key in the Hikmat Games folder. build.gradle reads BODHYA_KEYSTORE_PROPERTIES; nothing is
# copied into the repo.
if [ -f android/keystore.properties ]; then
  export BODHYA_KEYSTORE_PROPERTIES="$PWD/android/keystore.properties"
else
  export BODHYA_KEYSTORE_PROPERTIES="${BODHYA_KEYSTORE_PROPERTIES:-$HOME/code/Hikmat Games/android-app/android/keystore.properties}"
fi
if [ ! -f "$BODHYA_KEYSTORE_PROPERTIES" ]; then
  echo "STOP: no keystore.properties (looked for android/keystore.properties and"
  echo "      $BODHYA_KEYSTORE_PROPERTIES), so the release would be unsigned."
  echo "      Set BODHYA_KEYSTORE_PROPERTIES to the file beside the upload key. Never copy it into this repo."
  exit 1
fi

# The AAB ships ../index.html; the progress-safety harness tests ../hikmat/public/game.html.
# sync-game.sh writes both from the same source, so they must be byte-identical, or the gate
# below would pass a game that is not the one being shipped.
if ! cmp -s ../index.html ../hikmat/public/game.html; then
  echo "STOP: ../index.html and ../hikmat/public/game.html differ. Run ../sync-game.sh first."
  exit 1
fi

# A Play update gets one chance at her saved progress. Every save the harness knows (synthetic
# fixtures, and real tester saves in tests/saves/private/ when they are on this machine) is
# replayed from v7 through this build and back, in the web and the native mode, before an AAB is
# made at all. SKIP_E2E is for rebuilding the same tree right after a passing run; never for a
# changed game.
if [ "${SKIP_E2E:-}" = 1 ]; then
  echo "== SKIP_E2E=1: progress-safety harness NOT run (only for a tree that just passed it) =="
else
  # The harness's Python lives in the home folder. It used to be /tmp/pw, but macOS cleans /tmp of
  # files nobody has opened for a few days, and twice that broke the venv and stopped the gate here.
  E2E_PYTHON="${E2E_PYTHON:-$HOME/.venvs/bodhya-e2e/bin/python}"
  if ! "$E2E_PYTHON" -c 'import playwright' 2>/dev/null; then
    echo "STOP: $E2E_PYTHON has no Playwright. Set it up once, outside /tmp:"
    echo "      python3 -m venv ~/.venvs/bodhya-e2e && ~/.venvs/bodhya-e2e/bin/pip install playwright==1.64.1"
    echo "      ~/.venvs/bodhya-e2e/bin/playwright install chromium"
    echo "      or set E2E_PYTHON to another Python that has Playwright."
    exit 1
  fi
  echo "== Progress safety: e2e-progress-safety.py --mode both (about 10 minutes) =="
  ( cd .. && "$E2E_PYTHON" e2e-progress-safety.py --mode both ) || {
    echo "STOP: the progress-safety harness failed. No AAB was built."
    exit 1
  }
fi

echo "== Re-bundling game + syncing to android/ =="
npm run sync || { echo "SYNC FAILED"; exit 1; }

echo "== Building signed release bundle =="
cd android
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > local.properties
./gradlew --no-daemon :app:bundleRelease || { echo "BUILD FAILED"; exit 1; }

AAB="app/build/outputs/bundle/release/app-release.aab"
if [ -f "$AAB" ]; then
  # The release copy goes outside the repo, next to the Play listing material.
  PLAYSTORE="${BODHYA_PLAYSTORE_DIR:-$HOME/code/Hikmat Games/playstore}"
  if [ -d "$PLAYSTORE" ]; then
    cp "$AAB" "$PLAYSTORE/hikmat-release.aab"
    echo "OK -> $PLAYSTORE/hikmat-release.aab"
  else
    echo "OK -> android/$AAB (no $PLAYSTORE folder to copy it to)"
  fi
  "$JAVA_HOME/bin/jarsigner" -verify "$AAB" | head -1
else
  echo "AAB not produced"; exit 1
fi
