# ffdl

Downloads [fuckingfast.co](https://fuckingfast.co) links one by one, with progress, timing and resume.
It can also find the fuckingfast.co links on any page (such as a FitGirl repack page), so you can pick the ones you want.

It comes as a **desktop app** (`ffdl_gui.py`, or a packaged `ffdl.exe` / `ffdl.app` / `ffdl`) and as a
**command-line script** (`ffdl.py`). Both work on Windows, macOS and Linux.

## Download

Get the app for your system from the [Releases page](https://github.com/joakyng/ffdl/releases/latest):

| OS | File |
|---|---|
| Windows | `ffdl-windows.zip` → `ffdl.exe` |
| macOS | `ffdl-macos.zip` → `ffdl.app` |
| Linux | `ffdl-linux.tar.gz` → `ffdl` |

You also need [Google Chrome](https://www.google.com/chrome/) installed.

Or run it from source:

```
git clone https://github.com/joakyng/ffdl.git
cd ffdl
pip install -r requirements.txt
python ffdl_gui.py
```

## Requirements

- **Google Chrome**, on every OS. ffdl drives Chrome to get past Cloudflare.
- To run from source: **Python 3.8+** and `pip install -r requirements.txt`.
  On Linux you may also need Tk: `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora).
  No `playwright install` is needed, because ffdl uses your installed Chrome.

The packaged apps only need Chrome.

## Files

| File | What it is |
|---|---|
| `ffdl_gui.py` | Desktop app (Tkinter) |
| `ffdl.py` | Command-line script, and the shared download code the app uses |
| `build.py` | Builds the standalone app for the current OS with PyInstaller |
| `requirements.txt` | Python dependencies |
| `.github/workflows/build.yml` | Builds Windows, macOS and Linux apps on GitHub, and publishes releases |

## Desktop app

```
python ffdl_gui.py                      # from source
ffdl.exe                                # packaged (Windows)
ffdl.exe https://fitgirl-repacks.site/cyberpunk-2077/   # open and look up links right away
```

1. Paste fuckingfast.co links or a page URL into the box (**Paste** takes it from the clipboard), then press **Find links**.
2. The files are shown in groups. Each multipart archive gets its own group, named after the file without
   the `.partXX` piece (`game.part01.rar`, `game.part02.rar` → `game`). Every file that isn't a `.partXX`
   goes in **Files**, at the top.
3. Click the boxes to tick what you want: a group ticks or unticks all of its files (▣ means some are ticked).
   Nothing is ticked at first.
4. Choose **Save to** (your Downloads folder by default) and press **Download selected**.

The Status column shows each file's progress. The bar and the line under it show the current file's size, speed and time.
**Stop** ends the run; unfinished files resume next time. Closing the window during a download asks first.

## Command line

```
python ffdl.py                        # use the clipboard, or paste (end with an empty line)
python ffdl.py URL1 URL2 ...          # fuckingfast.co links as arguments
python ffdl.py links.txt              # a text file with links
python ffdl.py PAGE_URL               # list the fuckingfast.co links on a page
python ffdl.py page.html              # same, for a page saved from your browser
python ffdl.py -o "D:\Games" links.txt   # choose the output folder
```

| Input | Result |
|---|---|
| fuckingfast.co links (clipboard, pasted, arguments or a `.txt` file) | downloads them |
| Any other page URL | lists the fuckingfast.co links on it |
| A saved `.html` page | lists the fuckingfast.co links on it |

Typical use: run `python ffdl.py PAGE_URL`, copy the links you want from the terminal, then run
`python ffdl.py` with no arguments to download what's on the clipboard.

```
[1/103] Cyberpunk_2077_--_fitgirl-repacks.site_--_.part001.rar
   42.3% 866.0 MB / 2.0 GB  18.4 MB/s  0m 47s
  done in 1m 53s (17.9 MB/s average)
...
Total: 203.4 GB in 3h 12m 10s (17.6 MB/s average)
All done.
```

## How it works

- fuckingfast.co is behind Cloudflare, so a **minimized Chrome window** opens each file page and presses
  DOWNLOAD to get the file's direct link. Ad tabs are closed automatically.
- The file itself is downloaded by Python, which shows progress and supports resuming.
- If the Cloudflare check takes longer than 30 seconds, the Chrome window pops up so you can solve a captcha.
  It minimizes again when you're through.
- Headless (fully hidden) Chrome is not used because Cloudflare does not let it through.
- Chrome uses its own profile, so the Cloudflare check is usually passed automatically after the first time:

  | OS | Profile folder |
  |---|---|
  | Windows | `%LOCALAPPDATA%\ffdl\chrome-profile` |
  | macOS | `~/Library/Application Support/ffdl/chrome-profile` |
  | Linux | `~/.local/share/ffdl/chrome-profile` |

## Resuming and retries

- A file being downloaded is named `*.part`. If the download stops (Stop, Ctrl+C, connection loss, closing the app),
  start it again with the same links: finished files are skipped and the `.part` file continues where it stopped.
- Each file gets up to 5 attempts, with a fresh link each time. Files that still fail are listed at the end.

## Building the apps

```
pip install -r requirements.txt
python build.py
```

The result is in `dist/`: `ffdl.exe` on Windows, `ffdl.app` on macOS, `ffdl` on Linux. Each OS has to build its
own app, so GitHub Actions builds all three (`.github/workflows/build.yml`):

- **Test build:** Actions tab → **build** → **Run workflow**. The three apps are attached to the run.
- **Release:** push a version tag and the apps are published on the Releases page:

  ```
  git tag v1.0.0
  git push origin v1.0.0
  ```

- **macOS:** the app isn't signed, so the first time right-click it → **Open**.
- **Linux:** make it executable if needed: `chmod +x ffdl`.
- **Windows:** SmartScreen may warn about an unknown app → **More info** → **Run anyway**.

## Troubleshooting

**`ERR_CERT_COMMON_NAME_INVALID` or "could not open the page"**
The site is probably blocked by your internet provider through DNS. Setting the DNS server to `1.1.1.1`
is not enough if the provider intercepts plain DNS; use **encrypted DNS**:

- **Windows 11:** Settings → Network & internet → Wi-Fi (or Ethernet) → Hardware properties → DNS server
  assignment → Edit → Manual → IPv4 on. Preferred `1.1.1.1`, alternate `1.0.0.1`, both with
  **DNS over HTTPS: On (automatic template)**. Save, then run `ipconfig /flushdns`.
- **macOS:** install Cloudflare's [1.1.1.1 app](https://one.one.one.one/) (WARP or "1.1.1.1" DNS-only mode).
- **Linux (systemd-resolved):** in `/etc/systemd/resolved.conf` set `DNS=1.1.1.1#cloudflare-dns.com 1.0.0.1#cloudflare-dns.com`
  and `DNSOverTLS=yes`, then `sudo systemctl restart systemd-resolved`.

Alternatively, open the page in your browser, save it with Ctrl+S and use the saved `.html` file.

**"Google Chrome is not installed"**
Install it from https://www.google.com/chrome/. Chromium or other browsers are not used.

**"Could not start Chrome"**
Another copy of ffdl (app or script) is already running and using the Chrome profile. Wait for it to
finish or close it first.

**"could not get the direct download link"**
The site may have changed its download page. The DOWNLOAD button selector (`a.link-button`) is in
`resolve()` in `ffdl.py`.
