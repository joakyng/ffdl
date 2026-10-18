"""
Build a standalone ffdl app for the current OS with PyInstaller.

    pip install -r requirements.txt
    python build.py

Output in dist/: ffdl.exe (Windows), ffdl.app (macOS) or ffdl (Linux).
Each OS has to build its own app. Google Chrome must be installed where it runs.
"""
import sys

import PyInstaller.__main__

args = [
    "ffdl_gui.py",
    "--name", "ffdl",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--collect-all", "playwright",  # Playwright's driver; the browser is the installed Chrome
    # macOS apps are folders; elsewhere a single file is easier to share
    "--onedir" if sys.platform == "darwin" else "--onefile",
]
PyInstaller.__main__.run(args)
