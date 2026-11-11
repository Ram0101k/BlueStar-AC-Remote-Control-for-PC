# Blue Star AC - PC controller (unofficial)

A small Windows desktop app that controls a **Blue Star Smart AC** from your PC:
power, temperature, mode, fan speed, turbo, sleep, display lights and louvers.

> **Unofficial project.** Not affiliated with or endorsed by Blue Star. It uses the same
> cloud service as the official phone app, with *your own* account and *your own* AC.
> Use at your own risk; the service may change or block this at any time. "Blue Star" is a
> trademark of its owner.

## Features
- Native Windows window (no browser), dark theme, scales with the window size
- Live state from the AC, instant feedback when you press a button
- Several ACs on one account: switch between them with the buttons at the top
- Your password is **never saved**. Only your phone/email is remembered, and only if you tick the box.

## Download
Grab `BlueStarAC.exe` from the **Releases** page (or build it yourself, below). Windows
SmartScreen may warn about an unsigned app; that is normal for small open-source tools.

## Run from source
```
pip install -r requirements.txt
python bluestar_app.py
```
Needs Python 3.9+ with tkinter (the standard python.org installer includes it).
Keep `bluestar_app.py` and `bluestar_pc.py` in the same folder.

## Build the .exe
Double-click `build_exe.bat`, or push a tag like `v1.0.0` and let GitHub Actions build it.

## Adding a new AC
New ACs must be set up once with the **official phone app** (Bluetooth + Wi-Fi setup).
After that they appear here automatically when you sign in.

## How it works (short)
1. Sign in to Blue Star's REST API, which returns a session token and broker details.
2. Connect to AWS IoT over MQTT (WebSocket, SigV4-signed) and subscribe to the AC's state topic.
3. Buttons publish a "desired state" message for the AC.

## Known limits
- Signing in with an **email** uses a guessed login type; phone-number login is the tested path.
- Tested with a single model of AC. Mode/fan options come from your AC's own configuration.
- `bluestar_pc.py` also contains a small local web-page mode (`python bluestar_pc.py`).

## Privacy and safety
- Do not post screenshots or logs publicly without removing device IDs, phone numbers and tokens.
- Files the app writes live in `C:\Users\<you>\.bluestar_pc\` and are git-ignored.

## License
Choose one (MIT is a common choice for small tools) and add a `LICENSE` file.
