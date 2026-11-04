# Hikmat — Android (Capacitor) wrapper

Wraps the offline-first game (`../index.html`, which `../sync-game.sh` writes) as an Android app for the Google Play Store.
The whole game + curriculum are bundled, so the app is fully usable offline; when online it
syncs to the Frappe backend at `https://learn.bodhya.net`.

## How it works
- `bundle-game.js` copies `../index.html` into `www/index.html`, injecting
  `window.HIKMAT_BACKEND = "https://learn.bodhya.net"` right after `<head>` so API calls reach
  the remote server while bundled assets stay relative. It replicates the production
  `/assets/hikmat/*` layout (curriculum.json, icons, sw.js, manifest) under `www/` so the game's
  absolute asset paths resolve inside the WebView (origin `https://localhost`).
- `capacitor.config.json` sets `appId=net.bodhya.learn`, `appName=Bodhya`, `webDir=www`,
  `androidScheme=https`.
- Release signing reads `android/keystore.properties`, which points at the upload keystore. Neither
  is in git: this repo is public. `build.sh` stops if `keystore.properties` is missing, because
  Gradle would otherwise build an unsigned release.

## Toolchain (already installed on this machine)
- **JDK 21** (`brew install openjdk@21`) — Capacitor 8 requires 21.
- **Android SDK** cmdline-tools + `platforms;android-36` + `build-tools;36.0.0` + `platform-tools`
  (`brew install --cask android-commandlinetools`).
- Env for a build:
  ```bash
  export JAVA_HOME="$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home"
  export ANDROID_HOME="$(brew --prefix)/share/android-commandlinetools"
  export ANDROID_SDK_ROOT="$ANDROID_HOME"
  ```

## Build a signed release .aab
```bash
npm run sync          # re-bundle the game + copy web assets into android/
# bump versionCode in android/app/build.gradle for each new release
cd android
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > local.properties
./gradlew --no-daemon :app:bundleRelease
# → android/app/build/outputs/bundle/release/app-release.aab
```
The result is the file you upload to Play (a copy is kept in `../playstore/hikmat-release.aab`).

To install/test on a connected device instead: `cd android && ./gradlew installDebug`.

## Icons / splash
Regenerate from `assets/icon.png` (1024×1024):
```bash
npx @capacitor/assets generate --android --iconBackgroundColor '#fbf7ef' --iconBackgroundColorDark '#2c2440'
```

## ⚠️ Keep safe
The upload keystore and its password sign every update. They are kept outside this repo, on the
release machine, and must be backed up outside git. Back both up. With Play App Signing enabled, a lost *upload* key can be reset by Google, but
keep them anyway.
