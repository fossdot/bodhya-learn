/*
 * bundle-game.js — copies the canonical game into the Capacitor web root (www/)
 * and injects the packaged-build backend hook. Re-run after every edit to
 * ../index.html:   npm run bundle   (or  npm run sync  to also copy into android/)
 *
 * The game references its offline assets via absolute /assets/hikmat/* paths
 * (that is where Frappe serves them in production). We replicate that exact
 * layout under www/ so those paths resolve inside the WebView (origin https://localhost)
 * with NO changes to index.html itself.
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;                                   // android-app/
const GAME_SRC = path.resolve(ROOT, '..', 'index.html');  // apps/hikmat/index.html, written by sync-game.sh
const PUBLIC = path.resolve(ROOT, '..', 'hikmat', 'public');  // apps/hikmat/hikmat/public
const WWW = path.join(ROOT, 'www');
const ASSETS = path.join(WWW, 'assets', 'hikmat');
const BACKEND = 'https://learn.bodhya.net';               // online-sync target for the packaged app
// In-app brand for the packaged build. The Play STORE LISTING and the appId are still
// "Bodhya Learn" / net.bodhya.learn — this only changes what the app calls itself on screen,
// in its title and in the manifest short_name (Vishal, 2026-08-22).
const BRAND = 'Bodhya';                                   // Play Store build ships as Bodhya; the web app stays Hikmat

fs.rmSync(WWW, { recursive: true, force: true });
fs.mkdirSync(ASSETS, { recursive: true });

let html = fs.readFileSync(GAME_SRC, 'utf8');
const hook = `\n<script>window.HIKMAT_BACKEND=${JSON.stringify(BACKEND)};/* Capacitor packaged build: send API calls to the remote Frappe host; bundled assets stay relative */</script>`;
if (!/window\.HIKMAT_BACKEND\s*=/.test(html)) {
  html = html.replace(/<head>/i, '<head>' + hook);
  if (!html.includes(hook)) throw new Error('Could not find <head> to inject backend hook');
}
// ---------- Packaged-app branding ----------
// The Play Store build is branded "Bodhya Learn"; the web app served by Frappe stays "Hikmat".
// Rewritten here rather than in index.html so the canonical game keeps its own branding.
function sub(re, replacement, what) {
  const before = html;
  html = html.replace(re, replacement);
  if (html === before) throw new Error('Rebrand failed: could not rewrite ' + what);
}
sub(/<title>[^<]*<\/title>/i, '<title>' + BRAND + ' — Learn by Playing</title>', '<title>');
sub(/(<meta name="apple-mobile-web-app-title" content=")[^"]*(")/i, '$1' + BRAND + '$2', 'apple-mobile-web-app-title');
sub(/appName:\s*"[^"]*"/, 'appName: ' + JSON.stringify(BRAND), 'SETTINGS.appName default');

// Hikmat Settings on the backend returns appName + logo on every settings sync, which would flip a
// packaged app back to "Hikmat" the moment it goes online. Drop those assignments so the
// Play build keeps its brand both offline and online (tagline still follows the server).
for (const [re, what] of [
  [/SETTINGS\.appName = s\.appName;/g, 'server appName assignments'],
  [/SETTINGS\.logo = s\.logo \|\| "";/g, 'server logo assignments'],
]) {
  const hits = (html.match(re) || []).length;
  if (hits !== 2) throw new Error('Rebrand failed: expected 2 ' + what + ', found ' + hits);
  html = html.replace(re, '/* packaged build keeps its own brand */');
}

// ---------- "Help Needed" ----------
// The raise-hand button used to be cut here for the Play build (2026-08-19). On 2026-09-07 it was
// removed from index.html itself for every build, so there is nothing left to cut.

// ---------- A shared fail-loud helper for the Play-build-only cuts below ----------
// Every pattern is global, so `hits` is a real match count; a mismatch throws rather than
// silently shipping the feature. An index.html edit that moves this code breaks the BUILD,
// never the artifact — the same contract the rebrand transforms above rely on.
function cut(re, replacement, expected, what) {
  const hits = (html.match(re) || []).length;
  if (hits !== expected) throw new Error('Play-build cut failed: expected ' + expected + ' ' + what + ', found ' + hits);
  html = html.replace(re, replacement);
}

// ---------- Roshni AI tutor is web-only ----------
// The tutor hands a child a free-text box that posts to the backend's `ai_ask`. Every one of its
// surfaces is gated on SETTINGS.aiEnabled, which defaults false but is OVERWRITTEN from Hikmat
// Settings on every settings sync — so flipping one server row would put a generative-AI chat in
// front of children in already-installed copies, with no app update, no listing change and no
// Data safety change. That is exactly the Families behaviour-mismatch pattern enforcement looks
// for, so the Play build drops the feature outright (Vishal, 2026-08-19); the web build keeps it.
// Three cuts, belt-and-braces: the server flag can no longer turn it on, the floating button is
// never mounted, and both ai_ask calls are gone from the artifact — so "no AI chat in this
// release" is provable by grepping the shipped bundle instead of trusting a database row.
cut(/SETTINGS\.aiEnabled = !!s\.aiEnabled;/g,
    '/* Roshni AI is web-only: the packaged build ignores the server flag */',
    2, 'server aiEnabled assignments');
cut(/\n[ \t]*mountRoshniFab\(\);[^\n]*/g,
    '\n  /* Roshni AI button not mounted in the packaged Play build (web-only feature). */',
    1, 'mountRoshniFab() call');
// Both call sites read `const r = await api("ai_ask", {...}, true);` and both already handle a
// falsy `r` by showing the warm "I couldn't answer — tell the teacher" fallback, so replacing the
// call with null degrades exactly the way a failed request would.
cut(/await api\("ai_ask",[\s\S]*?\}, true\);/g,
    'null;   /* ai_ask removed from the packaged Play build (Roshni AI is web-only). */',
    2, 'ai_ask calls');

// ---------- Accounts are web-only ----------
// The web app at learn.bodhya.net/play keeps sign-in, sign-up, the campus roster and the
// facilitator reporting that hangs off them. The Play build ships with none of it (Vishal,
// 2026-08-27): a store app that opens by asking a child for an identity is asking before it
// has given her any reason to want one, and the two-lesson guest wall then stops her at the
// exact moment she is enjoying herself.
//
// One line does it. index.html reads AUTH_ENABLED at every account door — the ☰ sign-in row,
// the guest wall, the module-test wall, the boot-time social-signup resume and the five
// token-signed sync queues — so flipping it here removes all of them together and cannot
// leave one door open by accident. Progress is untouched and entirely local; retireAccounts()
// folds an already-signed-in tester's stars into the anonymous device profile on first launch
// after the update, so nobody upgrading loses anything.
//
// The auth SCREENS stay in the bundle as unreachable code, unlike the AI/YouTube cuts above,
// which had to be provably absent. Nothing here is a Play policy risk — it is a product
// decision — and keeping them means re-enabling accounts on Android is one line, not a revert.
// Matches either value: the web build turned accounts off too (2026-10-17), and this cut must
// still find exactly one declaration and pin it false whichever way index.html is set.
cut(/const AUTH_ENABLED = (true|false);/g,
    'const AUTH_ENABLED = false;  /* accounts are web-only in the packaged Play build */',
    1, 'AUTH_ENABLED declaration');

// ---------- WhatsApp group link is web-only ----------
// The ☰ menu's "Bodhya WhatsApp Group" row hands a child-directed app's users an invite into an
// open chat group on a third-party service with its own minimum age — a Families-policy question
// the Play build should not have to answer (2026-10-17). Three cuts: the menu row, its click
// handler, and the invite URL itself, so "no WhatsApp in this release" is provable by grepping the
// shipped bundle. The menu subtitle loses "and your group" with it. The web build keeps the row.
cut(/\n[ \t]*<a class="menurow" id="mWa"[\s\S]*?<\/a>/g,
    '', 1, 'WhatsApp group menu row');
cut(/\n[ \t]*sheet\.querySelector\("#mWa"\)\.onclick[^\n]*/g,
    '', 1, 'WhatsApp group click handler');
cut(/const WHATSAPP_GROUP = "[^"]*";/g,
    '/* WHATSAPP_GROUP removed for the packaged Play build (web-only). */', 1, 'WHATSAPP_GROUP constant');
cut(/tx\("Your friend, your settings, and your group\.","तुम्हारी सहेली, तुम्हारी सेटिंग और तुम्हारा समूह।"\)/g,
    'tx("Your friend and your settings.","तुम्हारी सहेली और तुम्हारी सेटिंग।")', 1, 'menu subtitle');

// ---------- YouTube embeds are web-only ----------
// renderVideo() prefers a YouTube iframe whenever a track/lesson videoUrl is a YouTube link. That
// iframe is NOT sandboxed (its own code comment wrongly says it is) and its allow list carries
// clipboard-write and web-share, neither of which playback needs. canStreamVideo() tests
// location.protocol, which androidScheme "https" already satisfies, so the branch is live inside
// the packaged app as soon as any track carries a YouTube URL. Dropping it makes the store answers
// about third-party content and unrestricted internet access an unconditional No on a
// child-directed app. The native <video> branch is deliberately untouched — a direct video file
// still streams online exactly as before, and the offline "needs internet" card is unchanged.
cut(/\n[ \t]*const embed = ytEmbedUrl\(V\.videoUrl\);\n[\s\S]*?\n    return;\n  \}/g,
    '\n  /* YouTube embed branch removed for the packaged Play build; the native <video> branch below still plays direct files. */',
    1, 'YouTube iframe branch');
// Now unreferenced, and they carry the only youtube.com/youtu.be strings in the bundle.
cut(/\n\/\/ YouTube: recognise[^\n]*\n\/\/ privacy-friendly[^\n]*\nfunction ytId\(u\)\{[^\n]*\nfunction ytEmbedUrl\(u\)\{[^\n]*/g,
    '\n/* ytId()/ytEmbedUrl() removed for the packaged Play build (YouTube embeds are web-only). */',
    1, 'ytId/ytEmbedUrl definitions');

fs.writeFileSync(path.join(WWW, 'index.html'), html);

function cp(src, dst) {
  if (fs.existsSync(src)) { fs.copyFileSync(src, dst); return true; }
  console.warn('  ! missing (skipped):', src);
  return false;
}
cp(path.join(PUBLIC, 'curriculum.json'), path.join(ASSETS, 'curriculum.json'));
cp(path.join(PUBLIC, 'testbank.json'), path.join(ASSETS, 'testbank.json'));
cp(path.join(PUBLIC, 'sw.js'), path.join(ASSETS, 'sw.js'));
const manifestDst = path.join(ASSETS, 'manifest.webmanifest');
if (cp(path.join(PUBLIC, 'manifest.webmanifest'), manifestDst)) {
  const m = JSON.parse(fs.readFileSync(manifestDst, 'utf8'));
  m.name = BRAND + ' — Learn by Playing';
  m.short_name = BRAND;
  fs.writeFileSync(manifestDst, JSON.stringify(m, null, 2));
}

const iconsSrc = path.join(PUBLIC, 'icons');
if (fs.existsSync(iconsSrc)) {
  const iconsDst = path.join(ASSETS, 'icons');
  fs.mkdirSync(iconsDst, { recursive: true });
  for (const f of fs.readdirSync(iconsSrc)) cp(path.join(iconsSrc, f), path.join(iconsDst, f));
}

console.log('Bundled game -> www/  (backend=' + BACKEND + ')');
