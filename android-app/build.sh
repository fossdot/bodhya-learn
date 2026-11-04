#!/bin/bash
# Rebuild a signed release .aab from the current ../index.html (run ../sync-game.sh first).
# Usage:  ./build.sh        (bump versionCode in android/app/build.gradle first for a new release)
set -uo pipefail
cd "$(dirname "$0")"

export JAVA_HOME="${JAVA_HOME:-$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home}"
export ANDROID_HOME="${ANDROID_HOME:-$(brew --prefix)/share/android-commandlinetools}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

# Signing material never enters this repo (it is public). keystore.properties points at the upload
# keystore, which lives outside git. Without it Gradle quietly builds an UNSIGNED release.
if [ ! -f android/keystore.properties ]; then
  echo "STOP: android/keystore.properties is missing, so the release would be unsigned."
  echo "      It is gitignored. Copy it in from the machine that holds the upload key."
  exit 1
fi

echo "== Re-bundling game + syncing to android/ =="
npm run sync

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
