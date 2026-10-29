"""
Sequential downloader for fuckingfast.co links.

Usage:
    python ffdl.py                      # use the clipboard, or paste (end with an empty line)
    python ffdl.py URL1 URL2 ...        # fuckingfast.co links as arguments
    python ffdl.py links.txt            # a text file with links
    python ffdl.py -o D:\\Games\\W3 links.txt
    python ffdl.py PAGE_URL             # any other page (e.g. a FitGirl repack page):
                                        # lists the fuckingfast.co links on it, so you can
                                        # copy the ones you want and run the script again
    python ffdl.py page.html            # same, for a page saved from your browser

A minimized Chrome window gets each file's direct link (the site sits behind
Cloudflare). If a captcha shows up, the window pops up; solve it and the script continues.
Files then download one by one with progress, and interrupted downloads resume.

ffdl_gui.py is a desktop UI built on the functions in this file.
"""
import argparse
import html
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from playwright.sync_api import sync_playwright


def data_dir():
    """Per-user app folder (the script folder isn't usable inside a packaged app)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "ffdl"


PROFILE_DIR = data_dir() / "chrome-profile"
DEFAULT_OUT = Path.home() / "Downloads"
LINK_RE = re.compile(r"https?://(?:www\.)?fuckingfast\.co/[A-Za-z0-9]+(?:#[^\s\"'<>]*)?")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
PART_RE = re.compile(r"^(.+?)\.part\d+\.[^.]+$", re.IGNORECASE)  # name.part01.rar
FILES_GROUP = "Files"
CHUNK = 1024 * 1024
MAX_TRIES = 5


class Cancelled(Exception):
    """The download was stopped through the `stop` event."""


# Terminal output for the CLI. The GUI passes its own callbacks instead.
_progress_line_open = False


def print_log(msg):
    global _progress_line_open
    if _progress_line_open:
        print()
        _progress_line_open = False
    print(msg)


def print_progress(done, total, speed, elapsed):
    global _progress_line_open
    pct = f"{done * 100 / total:5.1f}%" if total else ""
    print(f"\r  {pct} {fmt_size(done)} / {fmt_size(total)}  "
          f"{fmt_size(speed)}/s  {fmt_time(elapsed)}   ", end="", flush=True)
    _progress_line_open = True


def read_clipboard():
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def links_in(text):
    """fuckingfast.co links in text or HTML, in order, without duplicates."""
    return list(dict.fromkeys(LINK_RE.findall(html.unescape(text))))


def scrape(page, url, user_agent, log=print_log):
    """Return the fuckingfast.co links on a web page. Tries a plain request first,
    then Chrome (for Cloudflare or links added by JavaScript)."""
    log(f"Looking for fuckingfast.co links on {url}")
    try:
        r = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
        found = links_in(r.text) if r.ok else []
    except requests.RequestException:
        found = []
    if not found:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            wait_for_page(page, log=log)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass
            found = links_in("\n".join(frame.content() for frame in page.frames))
        except Exception as e:
            log(f"  could not open the page: {str(e).splitlines()[0]}\n"
                "  If it opens in your own browser, save it there (Ctrl+S) "
                "and use the saved .html file instead.")
            return []
    log(f"  found {len(found)} link(s)")
    return found


def links_from_text(text, page, user_agent, log=print_log):
    """Use the fuckingfast.co links in text; if there are none, scrape the pages it links to.
    Returns (links, whether pages were scraped)."""
    found = links_in(text)
    if found:
        return found, False
    for url in dict.fromkeys(URL_RE.findall(text)):
        found += scrape(page, url, user_agent, log)
    return found, True


def collect_links(sources, page, user_agent, log=print_log):
    """Returns (links, whether any of them came from scraping a page)."""
    texts, scraped = [], False
    for src in sources:
        if os.path.isfile(src):
            texts.append(Path(src).read_text(encoding="utf-8", errors="replace"))
            scraped = scraped or src.lower().endswith((".html", ".htm"))  # a saved page
        else:
            texts.append(src)
    if not sources:
        clip = read_clipboard()
        if URL_RE.search(clip):
            print("Using the clipboard.")
            texts.append(clip)
        else:
            print("Paste the links or a page URL, then press Enter on an empty line:")
            pasted = ""
            for line in sys.stdin:
                if not line.strip():
                    break
                pasted += line
            texts.append(pasted)
    links = []
    for text in texts:
        found, from_page = links_from_text(text, page, user_agent, log)
        links += found
        scraped = scraped or from_page
    return list(dict.fromkeys(links)), scraped


def group_links(links):
    """Group multipart archives by their name without the .partXX piece
    (game.part01.rar, game.part02.rar -> "game"). Files that aren't parts go
    in FILES_GROUP, which comes first; the other groups keep the page order."""
    groups = {FILES_GROUP: []}
    for url in links:
        m = PART_RE.match(name_from_url(url) or "")
        groups.setdefault(m.group(1) if m else FILES_GROUP, []).append(url)
    if not groups[FILES_GROUP]:
        del groups[FILES_GROUP]
    return groups


def name_from_url(url):
    return unquote(url.split("#", 1)[1]) if "#" in url else None


def set_window(page, state):
    """Minimize ("minimized") or restore ("normal") the Chrome window."""
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": state}})
        cdp.detach()
    except Exception:
        pass


def open_browser(p):
    """Start Chrome minimized. Headless Chrome doesn't get past Cloudflare."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False, accept_downloads=True,
            no_viewport=True,  # keep the real window size instead of a fixed 1280x720
            # Don't announce automation (navigator.webdriver, "controlled by automated
            # software" bar): Cloudflare checks for it
            ignore_default_args=["--enable-automation"],
            args=["--start-minimized", "--disable-blink-features=AutomationControlled"])
    except Exception as e:
        if "is not found" in str(e):
            raise RuntimeError("Google Chrome is not installed. Get it from "
                               "https://www.google.com/chrome/ and try again.") from None
        raise RuntimeError("Could not start Chrome. If ffdl is already running, "
                           "wait for it to finish or close it first.") from None
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    ctx.on("page", lambda popup: popup.close())  # ad tabs
    set_window(page, "minimized")
    return ctx, page


def wait_for_page(page, selector=None, timeout=300, log=print_log):
    """Wait until the page is past the Cloudflare check (and `selector` shows up).
    If that takes a while, show the window so you can solve a captcha."""
    ready = (f"document.querySelector({selector!r})" if selector
             else "!window._cf_chl_opt && document.readyState !== 'loading'")
    try:
        page.wait_for_function(ready, timeout=30_000)
        return
    except Exception:
        pass
    log("  Waiting for the Cloudflare check; solve it in the Chrome window if asked...")
    set_window(page, "normal")
    page.wait_for_function(ready, timeout=timeout * 1000)
    set_window(page, "minimized")


def resolve(page, url, log=print_log):
    """Open the file page in the browser, press DOWNLOAD and capture the direct link."""
    found = {}

    def on_response(resp):
        if resp.url.endswith("/go") and resp.headers.get("hx-redirect"):
            found["url"] = resp.headers["hx-redirect"]

    def on_download(dl):
        found.setdefault("url", dl.url)
        found["name"] = dl.suggested_filename
        dl.cancel()  # we download it ourselves, with progress and resume

    page.on("response", on_response)
    page.on("download", on_download)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        wait_for_page(page, "a.link-button", log=log)
        page.wait_for_load_state("networkidle", timeout=30_000)
        for _ in range(8):
            page.click("a.link-button")
            for _ in range(20):
                if "url" in found:
                    break
                page.wait_for_timeout(500)
            # Close any ad tabs the popup handler missed
            for other in page.context.pages:
                if other != page:
                    other.close()
            if "url" in found:
                break
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("download", on_download)
    if "url" not in found:
        raise RuntimeError("could not get the direct download link")
    return found["url"], found.get("name")


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def download(direct_url, dest, user_agent, progress=print_progress, stop=None):
    """Download to dest through a .part file, resuming it if it exists.
    Raises Cancelled when `stop` (a threading.Event) is set; the .part file is kept."""
    tmp = dest.with_name(dest.name + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": user_agent}
    if have:
        headers["Range"] = f"bytes={have}-"
    with requests.get(direct_url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:  # .part is already complete
            tmp.replace(dest)
            return
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            raise RuntimeError("got a web page instead of the file (link expired?)")
        if have and r.status_code != 206:
            have = 0  # server ignored the Range header, start over
        total = have + int(r.headers.get("Content-Length", 0))
        done, start, last = have, time.time(), 0
        with open(tmp, "ab" if have else "wb") as f:
            for chunk in r.iter_content(CHUNK):
                if stop is not None and stop.is_set():
                    raise Cancelled()
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if progress and now - last >= 0.5:
                    last = now
                    progress(done, total, (done - have) / max(now - start, 1e-6), now - start)
        if progress:
            now = time.time()
            progress(done, total, (done - have) / max(now - start, 1e-6), now - start)
    if total and tmp.stat().st_size < total:
        raise RuntimeError("connection dropped before the file finished")
    tmp.replace(dest)


def run_downloads(links, out, log=print_log, progress=print_progress, on_status=None,
                  stop=None):
    """Download the links one by one into `out`. Returns the links that failed.
    on_status(url, state) gets queued / resolving / downloading / done / skipped /
    failed / stopped. Setting `stop` ends the run; unfinished files resume next time."""
    status = on_status or (lambda url, state: None)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for url in links:
        status(url, "queued")
    log(f"{len(links)} link(s) -> {out.resolve()}\n")

    failed, run_bytes, run_start, current = [], 0, time.time(), None
    with sync_playwright() as p:
        ctx, page = open_browser(p)
        try:
            user_agent = page.evaluate("navigator.userAgent")
            for i, url in enumerate(links, 1):
                current = url
                if stop is not None and stop.is_set():
                    raise Cancelled()
                name = name_from_url(url)
                log(f"[{i}/{len(links)}] {name or url}")
                if name and (out / name).exists():
                    log("  already downloaded, skipping")
                    status(url, "skipped")
                    continue
                part = out / f"{name}.part"
                file_start = time.time()
                already = part.stat().st_size if name and part.exists() else 0
                for attempt in range(1, MAX_TRIES + 1):
                    try:
                        status(url, "resolving")
                        direct, suggested = resolve(page, url, log)
                        name = name or suggested or url.rsplit("/", 1)[-1]
                        if (out / name).exists():
                            log("  already downloaded, skipping")
                            status(url, "skipped")
                            break
                        status(url, "downloading")
                        download(direct, out / name, user_agent, progress, stop)
                        took = time.time() - file_start
                        fetched = (out / name).stat().st_size - already
                        run_bytes += fetched
                        log(f"  done in {fmt_time(took)} "
                            f"({fmt_size(fetched / max(took, 1e-6))}/s average)")
                        status(url, "done")
                        break
                    except Cancelled:
                        raise
                    except Exception as e:
                        log(f"  attempt {attempt}/{MAX_TRIES} failed: {e}")
                        if stop is not None:
                            if stop.wait(5 * attempt):
                                raise Cancelled()
                        else:
                            time.sleep(5 * attempt)
                else:
                    failed.append(url)
                    status(url, "failed")
        except Cancelled:
            status(current, "stopped")
            log("Stopped. Start it again with the same links to resume.")
        finally:
            ctx.close()

    took = time.time() - run_start
    log(f"\nTotal: {fmt_size(run_bytes)} in {fmt_time(took)} "
        f"({fmt_size(run_bytes / max(took, 1e-6))}/s average)")
    log("All done." if not failed else "Failed:\n  " + "\n  ".join(failed))
    return failed


def main():
    ap = argparse.ArgumentParser(description="Download fuckingfast.co links one by one.")
    ap.add_argument("links", nargs="*",
                    help="fuckingfast.co links, pages that contain them, or a text/.html file")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT),
                    help="output folder (default: your Downloads folder)")
    args = ap.parse_args()

    with sync_playwright() as p:
        ctx, page = open_browser(p)
        user_agent = page.evaluate("navigator.userAgent")
        try:
            links, scraped = collect_links(args.links, page, user_agent)
        finally:
            ctx.close()
    if not links:
        sys.exit("No fuckingfast.co links found.")
    if scraped:
        print("\n" + "\n".join(links))
        print(f"\n{len(links)} link(s). Copy the ones you want and run the script again.")
        return

    print()
    try:
        run_downloads(links, args.out)
    except KeyboardInterrupt:
        print_log("\nStopped. Run again with the same links to resume.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:  # e.g. Chrome missing or already in use
        sys.exit(str(e))
