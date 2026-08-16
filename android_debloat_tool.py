#!/usr/bin/env python3
"""
Android Debloat Tool
A "WinUtil for Android" — remove/disable/restore OEM bloatware over adb,
without root. Built around real findings from a Tecno/Transsion (HiOS) device,
but the core engine works on any adb-connected Android phone.

Requires: adb installed and on PATH, USB debugging enabled on the phone,
device authorized (run `adb devices` once and accept the prompt on-phone).

Usage:
    python3 android_debloat_tool.py
"""

import subprocess
import sys
import shutil
import datetime
import json
import os
import platform

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debloat_log.txt")

# ---------------------------------------------------------------------------
# Curated package database
# ---------------------------------------------------------------------------
# risk levels:
#   "safe"      - confirmed removable, no functional loss for most users
#   "caution"   - removable but may break a feature you actually use
#   "protected" - never touch; can break calls/network/radio/security
#
# category groups apps for menu display.

APP_DB = {
    "AI / Assistant bloat": {
        "com.transsion.ella": {"risk": "safe", "note": "Voice assistant onboarding shell (real pkg is aivoiceassistant)"},
        "com.transsion.aivoiceassistant": {"risk": "safe", "note": "Ella voice assistant — actual app package"},
        "com.transsion.aisupportercore": {"risk": "safe", "note": "Backend companion to the voice assistant"},
        "com.transsion.aiwriting": {"risk": "safe", "note": "AI writing assistant"},
        "com.transsion.aiassistantlifestyle": {"risk": "safe", "note": "'Life' AI lifestyle app"},
        "com.transsion.kolun.aiservice": {"risk": "caution", "note": "Core AI service — some phone features may depend on it"},
        "com.transsion.aicore.main": {"risk": "caution", "note": "AI core model manager"},
        "com.transsion.aicore.llm": {"risk": "caution", "note": "On-device LLM component"},
        "com.transsion.aicore.ocr": {"risk": "caution", "note": "OCR engine — may be used by camera/gallery text scan"},
        "com.transsion.aicore.cv": {"risk": "caution", "note": "Computer vision core"},
    },
    "Ad-driven app stores / installers": {
        "com.transsnet.store": {"risk": "safe", "note": "Palm Store — pushes ads/installs from apps like MovieBox. Removing can break in-app 'Install' buttons that hardcode this package."},
        "tech.palm.id": {"risk": "caution", "note": "Palm ID — account/auth service tied to Palm Store"},
    },
    "Fake VPN / proxy bloat": {
        "secure.unblock.unlimited.proxy.snap.hotspot.shield": {"risk": "safe", "note": "Bundled free VPN, generally untrustworthy, redundant if you use a real VPN"},
    },
    "Caller-ID / contact-harvesting": {
        "com.sh.smart.caller": {"risk": "caution", "note": "WARNING: on some HiOS builds this IS the phone dialer app, not just caller-ID. Confirmed to break calling on this device. Verify with 'Check what handles calls' before removing."},
    },
    "'Booster' / cleaner nagware": {
        "com.transsion.phonemaster": {"risk": "safe", "note": "Fake cleaner/booster, mostly ads and bogus junk-file warnings"},
        "com.transsion.batterylab": {"risk": "safe", "note": "Battery 'optimizer' nagware"},
        "com.transsion.spacesaversdk": {"risk": "safe", "note": "Storage 'optimizer' nagware"},
    },
    "Telemetry / tracking": {
        "com.transsion.uxdetector": {"risk": "safe", "note": "Usage/UX telemetry tracker"},
        "com.transsion.statisticalsales": {"risk": "safe", "note": "Sales/usage statistics reporting"},
        "com.idea.questionnaire": {"risk": "safe", "note": "Carrier survey/telemetry tool"},
    },
    "Redundant OEM duplicates": {
        "com.gallery20": {"risk": "caution", "note": "AI Gallery — redundant only if you use Google Photos"},
        "com.transsion.chromecustomization": {"risk": "safe", "note": "Chrome tweaker — pointless if you use another browser"},
        "com.transsion.calculator": {"risk": "caution", "note": "OEM calculator — redundant if you use another"},
        "com.transsion.deskclock": {"risk": "caution", "note": "OEM clock app — redundant if you use another"},
        "com.transsion.notebook": {"risk": "caution", "note": "OEM notepad — redundant if you use another"},
        "com.transsion.fmradio": {"risk": "caution", "note": "FM radio app, only useful if your phone has a radio chip you use"},
    },
    "Misc / app-locker / unclear": {
        "com.xui.xhide": {"risk": "safe", "note": "App-hiding/locker utility, not something most people asked for"},
        "com.hoffnung": {"risk": "caution", "note": "Unclear generic-named app — inspect before removing"},
        "com.splendapps.checkmate": {"risk": "caution", "note": "Unclear third-party app — inspect before removing"},
    },
    "PROTECTED — never touch": {
        "com.android.server.telecom": {"risk": "protected", "note": "Core call-handling framework"},
        "com.android.providers.telephony": {"risk": "protected", "note": "SMS/MMS/cellular data provider"},
        "com.android.phone": {"risk": "protected", "note": "Telephony service"},
        "com.mediatek.engineermode": {"risk": "protected", "note": "Radio/modem engineering mode — can brick cellular"},
        "com.google.android.gms": {"risk": "protected", "note": "Google Play Services — breaks most apps if removed"},
        "com.google.android.gsf": {"risk": "protected", "note": "Google Services Framework"},
        "com.android.vending": {"risk": "protected", "note": "Google Play Store"},
        "com.transsion.hilauncher": {"risk": "protected", "note": "Your home screen launcher — clearing its cache resets layout, don't uninstall"},
    },
}

# ---------------------------------------------------------------------------
# adb helpers
# ---------------------------------------------------------------------------

def guided_adb_install():
    system = platform.system()
    print("\n'adb' isn't installed or isn't on your PATH.")
    print("You do NOT need Android Studio for this — adb ships on its own")
    print("as 'platform-tools', a tiny download with no IDE attached.\n")

    if system == "Linux":
        print("Detected: Linux")
        if shutil.which("apt"):
            print("  Run:   sudo apt update && sudo apt install android-tools-adb")
        elif shutil.which("dnf"):
            print("  Run:   sudo dnf install android-tools")
        elif shutil.which("pacman"):
            print("  Run:   sudo pacman -S android-tools")
        else:
            print("  No known package manager detected — download platform-tools directly:")
            print("  https://developer.android.com/tools/releases/platform-tools")
            print("  Unzip it, then either move 'adb' to /usr/local/bin or add the")
            print("  unzipped folder to your PATH.")
    elif system == "Darwin":
        print("Detected: macOS")
        print("  Easiest: install Homebrew (https://brew.sh) if you don't have it, then:")
        print("  Run:   brew install android-platform-tools")
    elif system == "Windows":
        print("Detected: Windows")
        print("  Download platform-tools (zip, no installer, no Android Studio needed):")
        print("  https://developer.android.com/tools/releases/platform-tools")
        print("  Unzip it somewhere permanent (e.g. C:\\platform-tools), then add that")
        print("  folder to your PATH: Settings > System > About > Advanced system")
        print("  settings > Environment Variables > Path > New > paste the folder.")
    else:
        print(f"Unrecognized OS ({system}). Grab platform-tools manually:")
        print("  https://developer.android.com/tools/releases/platform-tools")

    print("\nAfter installing, close and reopen your terminal, then run this script again.")
    sys.exit(1)


def guided_phone_setup():
    print("\nNo authorized device found. On your PHONE, do this once:")
    print("  1. Settings -> About phone -> tap 'Build number' 7 times")
    print("     (this unlocks Developer options — you'll see a toast counting down)")
    print("  2. Settings -> Developer options -> turn on 'USB debugging'")
    print("  3. Plug the phone into this computer with a USB cable")
    print("  4. A popup will appear ON THE PHONE screen asking")
    print("     'Allow USB debugging?' — check 'Always allow from this computer'")
    print("     and tap Allow. (If you don't see it, unlock the phone screen —")
    print("     it sometimes hides behind the lock screen.)")
    print("\nThen run this script again.")
    sys.exit(1)


def check_adb():
    if shutil.which("adb") is None:
        guided_adb_install()

    try:
        result = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        print("adb didn't respond. Try unplugging and replugging the phone, then rerun.")
        sys.exit(1)

    lines = [l for l in result.stdout.strip().splitlines()[1:] if l.strip()]
    if not lines:
        guided_phone_setup()

    authorized = [l for l in lines if l.strip().endswith("device")]
    unauthorized = [l for l in lines if "unauthorized" in l]

    if unauthorized and not authorized:
        print("\nDevice detected but shows as UNAUTHORIZED.")
        print("Look at your PHONE screen right now — there should be an")
        print("'Allow USB debugging?' popup waiting for you to tap Allow.")
        print("If you don't see it: unlock the phone, unplug and replug the cable.")
        sys.exit(1)

    if not authorized:
        print("Device connected but in an unexpected state:")
        for l in lines:
            print(f"  {l}")
        print("Try unplugging and replugging the USB cable.")
        sys.exit(1)

    print(f"Device connected: {authorized[0].split()[0]}")


def adb_shell(*args):
    result = subprocess.run(["adb", "shell", *args], capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()


def is_installed(pkg):
    out, _ = adb_shell("pm", "list", "packages", "--user", "0")
    return f"package:{pkg}" in out


def is_disabled(pkg):
    out, _ = adb_shell("pm", "list", "packages", "-d")
    return f"package:{pkg}" in out


def uninstall(pkg):
    out, err = adb_shell("pm", "uninstall", "--user", "0", pkg)
    ok = "Success" in out
    log(f"UNINSTALL {pkg}: {'OK' if ok else 'FAILED - ' + out + err}")
    return ok, out or err


def disable(pkg):
    out, err = adb_shell("pm", "disable-user", "--user", "0", pkg)
    ok = "new state" in out
    log(f"DISABLE {pkg}: {'OK' if ok else 'FAILED - ' + out + err}")
    return ok, out or err


def restore(pkg):
    out, err = adb_shell("cmd", "package", "install-existing", pkg)
    ok = "installed for user" in out
    log(f"RESTORE {pkg}: {'OK' if ok else 'FAILED - ' + out + err}")
    return ok, out or err


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a") as f:
        f.write(f"[{ts}] {msg}\n")


def current_focus():
    """Print the package name of whatever's currently on screen — helps
    identify unknown app icons (open the app on the phone first)."""
    out, _ = adb_shell("dumpsys", "window")
    for line in out.splitlines():
        if "mCurrentFocus" in line:
            print(line.strip())
            return
    print("Could not read current focus — is the screen on?")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def risk_tag(risk):
    return {"safe": "[SAFE]", "caution": "[CAUTION]", "protected": "[PROTECTED]"}[risk]


def status_tag(pkg):
    if is_installed(pkg):
        return "installed"
    if is_disabled(pkg):
        return "disabled"
    return "removed"


def print_category(name, apps):
    print(f"\n=== {name} ===")
    for i, (pkg, meta) in enumerate(apps.items(), 1):
        st = status_tag(pkg)
        print(f" {i:2d}. {risk_tag(meta['risk']):12s} {pkg:45s} ({st})")
        print(f"      {meta['note']}")


def category_menu():
    cats = list(APP_DB.keys())
    while True:
        print("\n--- Android Debloat Tool ---")
        for i, c in enumerate(cats, 1):
            print(f" {i}. {c}")
        print(" 0. Identify current foreground app (open it on phone first)")
        print(" q. Quit")
        choice = input("Pick a category: ").strip().lower()
        if choice == "q":
            break
        if choice == "0":
            current_focus()
            continue
        try:
            idx = int(choice) - 1
            cat_name = cats[idx]
        except (ValueError, IndexError):
            print("Invalid choice.")
            continue
        app_menu(cat_name, APP_DB[cat_name])


def app_menu(cat_name, apps):
    while True:
        print_category(cat_name, apps)
        print(" u <n>  - uninstall app n")
        print(" d <n>  - disable app n")
        print(" r <n>  - restore app n")
        print(" b      - back")
        cmd = input("Command: ").strip().lower()
        if cmd == "b":
            return
        parts = cmd.split()
        if len(parts) != 2 or parts[0] not in ("u", "d", "r"):
            print("Format: u 3 / d 3 / r 3")
            continue
        try:
            idx = int(parts[1]) - 1
            pkg = list(apps.keys())[idx]
            meta = apps[pkg]
        except (ValueError, IndexError):
            print("Invalid app number.")
            continue

        if meta["risk"] == "protected":
            print("This app is PROTECTED. Refusing to touch it.")
            continue

        if parts[0] == "u":
            if meta["risk"] == "caution":
                confirm = input(f"CAUTION: {meta['note']}\nType YES to uninstall {pkg}: ")
                if confirm != "YES":
                    print("Cancelled.")
                    continue
            ok, out = uninstall(pkg)
        elif parts[0] == "d":
            ok, out = disable(pkg)
        else:
            ok, out = restore(pkg)

        print(("OK: " if ok else "FAILED: ") + out)


if __name__ == "__main__":
    check_adb()
    category_menu()
    print(f"\nSession log saved to {LOG_PATH}")
