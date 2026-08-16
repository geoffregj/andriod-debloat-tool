# Android Debloat Tool

A "WinUtil for Android" — remove, disable, or restore OEM/carrier
bloatware over `adb`, with no root required. Ships as both a terminal
tool and a local browser UI. No Android Studio needed to run it — just
Python 3 and `adb`.

Built from real, on-device findings on a Tecno Spark 40 (Transsion /
HiOS), but the engine works on any adb-connected Android phone.
Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) to add
packages for other OEMs (Xiaomi/MIUI, Samsung/One UI, Oppo/ColorOS,
etc).

![risk tags](https://img.shields.io/badge/safety-SAFE%20%7C%20CAUTION%20%7C%20PROTECTED-informational)

## Why this exists

OEM Android skins (especially budget phones from Transsion brands —
Tecno, Infinix, itel — but also Xiaomi, Samsung, and others) ship dozens
of preinstalled apps: ad-injecting "stores," fake AI assistants,
telemetry trackers, and "booster" nagware. Most of it can be removed
per-user without root via `pm uninstall --user 0`, but:

- **Display names lie.** The app called "Ella" on-screen is not the
  package called `com.transsion.ella` — that's a decoy. The real
  package is `com.transsion.aivoiceassistant`. Uninstalling the wrong
  one does nothing, silently.
- **Some "bloat" isn't actually bloat.** `com.sh.smart.caller` looks
  like caller-ID spyware from the name alone — but on some HiOS builds
  it **is the phone dialer app**, and removing it breaks outgoing calls
  entirely.
- **Removing an app store can break other apps.** Some ad-driven apps
  hardcode an explicit intent to their OEM's app store package (e.g.
  Palm Store) to handle "Install" buttons. Remove the store, and that
  button silently fails instead of falling back to Play Store — this is
  how Android intent resolution works, not a bug.

This tool exists to make removal **safe and reversible** by (a) telling
you honestly what's known vs. guessed about each package, (b) letting
you visually confirm what an app actually is before touching it, and
(c) refusing point-blank to touch anything that could break your phone.

## Two ways to use it

### CLI — `android_debloat_tool.py`

A menu-driven terminal tool. Zero dependencies beyond the Python
standard library.

```bash
python3 android_debloat_tool.py
```

- Browse the curated app database by category
- `u <n>` uninstall, `d <n>` disable, `r <n>` restore, by list number
- `CAUTION`-tagged apps require typing `YES` to confirm
- `PROTECTED`-tagged apps are refused outright — the tool won't let you
  remove them even if you try
- Option `0` prints the package name of whatever's currently open on
  the phone screen — open a mystery app, then use this to identify its
  real package before deciding anything
- Every action is timestamped into `debloat_log.txt`
- If `adb` isn't installed or the phone isn't authorized, the tool
  detects your OS and prints exact setup commands instead of just
  failing

### Web UI — `webui.py`

The same engine (imports `android_debloat_tool.py` directly — no
duplicated logic), with a local browser dashboard on top:

```bash
python3 webui.py
```

Opens `http://127.0.0.1:8765` automatically. Still stdlib-only — the
server is built on Python's `http.server`, no Flask/Django, no `pip
install` needed.

What it adds over the CLI:
- **Shows every app actually installed on the phone**, not just the
  curated list — anything not in the database is auto-classified:
  known Android/Google system prefixes and anything with "launcher" in
  the name are locked as `PROTECTED` automatically, everything else is
  flagged `UNKNOWN` and requires confirmation before any action
- **"Open on phone" button per app** — launches it live on the device
  so you can look at the screen and confirm what it actually is before
  uninstalling. This directly targets the Ella-decoy problem: don't
  trust the package name, look at the app
- Search/filter box across package names and notes
- A live action log at the bottom of the page, same data as
  `debloat_log.txt`
- Same server-side safety rules as the CLI — risk is **re-derived on
  the server** for every action, never trusted from the browser, so a
  tampered client-side request still can't touch a `PROTECTED` package

The web UI only binds to `127.0.0.1` (localhost) — it's not reachable
from your network, only from the same machine running it.

## Setup (first time / no adb yet)

See [SETUP.md](SETUP.md) for a full zero-to-running walkthrough,
written for someone who's never used `adb` before — installing
platform-tools per OS, enabling Developer options and USB debugging on
the phone, and confirming the connection. Both the CLI and the tool's
own error messages will also guide you through this if something's
missing when you run it.

Quick version, if you already know the drill:
```bash
adb devices   # should show your phone as "device", not "unauthorized"
python3 android_debloat_tool.py   # or: python3 webui.py
```

## Repo layout

```
android-debloat-tool/
├── android_debloat_tool.py   # core engine + CLI — the curated app DB lives here
├── webui.py                  # browser front end, imports the CLI module directly
├── README.md                 # this file
├── SETUP.md                  # adb / USB debugging setup for first-time users
├── CONTRIBUTING.md           # how to add packages or support a new OEM
├── LICENSE                   # MIT
└── debloat_log.txt           # created on first run, gitignored
```

## Safety model

- Every removal goes through `pm uninstall --user 0`, which unlinks the
  app from your user profile without deleting the underlying system
  APK. Nothing here requires root.
- Every removal is reversible with the `restore` action (`cmd package
  install-existing`) — as long as you haven't since done a factory
  reset or system update that actually purges it.
- Every package is tagged one of three ways, and the tags mean
  something specific:
  - **`SAFE`** — confirmed on a real device, no functional loss for a
    typical user.
  - **`CAUTION`** — removable, but might be load-bearing for someone.
    Requires typed `YES` confirmation (CLI) or a confirm dialog + typed
    `YES` (web UI) before acting.
  - **`PROTECTED`** — refused by the tool itself, server-side in the
    web UI and function-level in the CLI, regardless of what button you
    click or what number you type. Reserved for things that can break
    calling, networking, security, or bootability.
- In the web UI, anything not yet in the curated database gets a
  same-spirit auto-classification (known system prefixes →
  `PROTECTED`, everything else → `UNKNOWN` requiring confirmation) so
  browsing the full app list is never a foot-gun by default.

## Known gotchas (found the hard way, documented so nobody repeats them)

- **`com.transsion.ella` is a decoy.** The real "Ella" voice assistant
  package is `com.transsion.aivoiceassistant`. Always confirm with
  "Open on phone" (web UI) or option `0` (CLI) before assuming a
  display name maps to the package you expect.
- **`com.sh.smart.caller` can BE the phone dialer**, not just a
  caller-ID add-on, on some HiOS builds. Confirmed to break outgoing
  calls on a real device. That's why it's tagged `CAUTION`, not `SAFE`.
- **Removing an ad-driven app store can break in-app "Install"
  buttons** in apps that hardcode an explicit intent to that store
  package. Expect a silent failure or a crash in the calling app, not a
  graceful fallback to Play Store.
- **Launcher icons can go stale after uninstall.** This is cosmetic —
  the launcher's icon cache hasn't refreshed, not a sign the removal
  failed. Removing the dead icon manually is safer than clearing the
  launcher's cache, which resets your whole home screen layout.
- **`am start -n` on a bare `ResolverActivity`/`ChooserActivity`
  throws "no app can perform this action."** Those aren't real launcher
  entry points — they expect to be invoked as part of another app's
  intent resolution, not called directly. `webui.py`'s `open_app()`
  detects and skips these, falling back to `monkey -p <pkg> -c
  android.intent.category.LAUNCHER 1`, which finds the real entry point
  regardless of the app's internal activity structure.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — the whole tool is built around
one dictionary (`APP_DB` in `android_debloat_tool.py`); extending it
for a new OEM or adding a package you've found is mostly just adding an
entry, with guidance on how to verify a risk tag honestly before
committing to it.

## License

MIT — see [LICENSE](LICENSE). Do whatever you want with it, just don't
blame me if you remove something you shouldn't have. Read the risk tag.
