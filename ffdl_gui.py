"""
Desktop UI for ffdl (Windows, macOS, Linux).

Paste fuckingfast.co links or a page URL (e.g. a FitGirl repack page), press
Find links, tick the files you want and press Download selected.
Links or a page URL can also be given on the command line.
"""
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from playwright.sync_api import sync_playwright

import ffdl

ON, OFF, SOME = "☑", "☐", "▣"  # ☑ ☐ ▣
STATUS_TEXT = {
    "queued": "queued",
    "resolving": "getting link…",
    "downloading": "⏵ downloading",
    "done": "✓ done",
    "skipped": "✓ already there",
    "failed": "✗ failed",
    "stopped": "stopped",
}


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()  # worker thread -> UI
        self.stop = threading.Event()
        self.worker = None
        self.closing = False
        self.links = []          # all links, in page order
        self.item_of = {}        # url -> tree item
        self.url_of = {}         # tree item -> url (files only)
        self.label_of = {}       # tree item -> text without the checkbox
        self.on = set()          # ticked file items
        self.job_links = []      # links of the running download
        self.finished = 0

        root.title("ffdl")
        root.geometry("920x720")
        root.minsize(700, 520)
        style = ttk.Style()
        if sys.platform.startswith("linux") and "clam" in style.theme_names():
            style.theme_use("clam")
        self.build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    # ---------- layout ----------

    def build(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        ttk.Label(main, text="Links or page URL:").grid(row=0, column=0, sticky="w")
        top = ttk.Frame(main)
        top.grid(row=1, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self.input = tk.Text(top, height=4, wrap="none", undo=True)
        self.input.grid(row=0, column=0, rowspan=2, sticky="ew")
        self.paste_btn = ttk.Button(top, text="Paste", command=self.paste)
        self.paste_btn.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.find_btn = ttk.Button(top, text="Find links", command=self.find_links)
        self.find_btn.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

        out_row = ttk.Frame(main)
        out_row.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        out_row.columnconfigure(1, weight=1)
        ttk.Label(out_row, text="Save to:").grid(row=0, column=0, padx=(0, 6))
        self.out_var = tk.StringVar(value=str(ffdl.DEFAULT_OUT))
        ttk.Entry(out_row, textvariable=self.out_var).grid(row=0, column=1, sticky="ew")
        self.browse_btn = ttk.Button(out_row, text="Browse…", command=self.browse)
        self.browse_btn.grid(row=0, column=2, padx=(8, 0))

        tree_frame = ttk.Frame(main)
        tree_frame.grid(row=3, column=0, sticky="nsew")
        main.rowconfigure(3, weight=3)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=("status",), selectmode="browse")
        self.tree.heading("#0", text="File", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", stretch=True, width=600)
        self.tree.column("status", stretch=False, width=150)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<space>", lambda e: self.toggle(self.tree.focus()))

        actions = ttk.Frame(main)
        actions.grid(row=4, column=0, sticky="ew", pady=8)
        self.download_btn = ttk.Button(actions, text="Download selected", command=self.start_download)
        self.download_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="Stop", command=self.request_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.count_var = tk.StringVar(value="No links yet.")
        ttk.Label(actions, textvariable=self.count_var).pack(side="right")

        self.current_var = tk.StringVar()
        ttk.Label(main, textvariable=self.current_var).grid(row=5, column=0, sticky="w")
        self.bar = ttk.Progressbar(main, maximum=100)
        self.bar.grid(row=6, column=0, sticky="ew", pady=(2, 2))
        self.stats_var = tk.StringVar()
        ttk.Label(main, textvariable=self.stats_var).grid(row=7, column=0, sticky="w")

        self.log_box = ScrolledText(main, height=8, state="disabled", wrap="word")
        self.log_box.grid(row=8, column=0, sticky="nsew", pady=(8, 0))
        main.rowconfigure(8, weight=1)

    # ---------- tree with checkboxes ----------

    def fill_tree(self, links):
        self.tree.delete(*self.tree.get_children())
        self.links = links
        self.item_of, self.url_of, self.label_of, self.on = {}, {}, {}, set()
        # Nothing is ticked at first: tick a group to select all of its files
        for label, urls in ffdl.group_links(links).items():
            item = self.tree.insert("", "end")
            self.label_of[item] = f"{label} ({len(urls)})"
            for url in urls:
                child = self.tree.insert(item, "end")
                self.label_of[child] = ffdl.name_from_url(url) or url
                self.url_of[child], self.item_of[url] = url, child
        self.refresh()

    def files_under(self, item):
        kids = self.tree.get_children(item)
        if not kids:
            return [item] if item in self.url_of else []
        return [f for k in kids for f in self.files_under(k)]

    def refresh(self, items=None):
        for item in self.tree.get_children(items or ""):
            files = self.files_under(item)
            ticked = sum(f in self.on for f in files)
            box = ON if ticked == len(files) else OFF if ticked == 0 else SOME
            self.tree.item(item, text=f"{box} {self.label_of[item]}")
            self.refresh(item)
        if items is None:
            self.count_var.set(f"{len(self.on)} of {len(self.url_of)} files selected")

    def on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "tree":
            return
        if "indicator" in self.tree.identify_element(event.x, event.y):
            return  # the expand/collapse arrow
        self.toggle(self.tree.identify_row(event.y))

    def toggle(self, item):
        if not item or self.busy():
            return
        files = self.files_under(item)
        if all(f in self.on for f in files):
            self.on.difference_update(files)
        else:
            self.on.update(files)
        self.refresh()

    def selected_links(self):
        return [url for url in self.links if self.item_of[url] in self.on]

    # ---------- actions ----------

    def paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)

    def browse(self):
        folder = filedialog.askdirectory(initialdir=self.out_var.get() or str(ffdl.DEFAULT_OUT))
        if folder:
            self.out_var.set(folder)

    def find_links(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("ffdl", "Paste fuckingfast.co links or a page URL first.")
            return

        def job():
            links = ffdl.links_in(text)
            if links:
                return links
            with sync_playwright() as p:
                ctx, page = ffdl.open_browser(p)
                try:
                    user_agent = page.evaluate("navigator.userAgent")
                    return ffdl.collect_links([text], page, user_agent, log=self.log)[0]
                finally:
                    ctx.close()

        self.run_job("find", job)

    def start_download(self):
        links = self.selected_links()
        if not links:
            messagebox.showinfo("ffdl", "Tick the files you want to download first.")
            return
        out = self.out_var.get().strip() or str(ffdl.DEFAULT_OUT)
        for item in self.url_of:
            self.tree.set(item, "status", "")
        self.job_links, self.finished = links, 0
        self.stop.clear()
        self.run_job("download", lambda: ffdl.run_downloads(
            links, out, log=self.log, progress=self.on_progress,
            on_status=self.on_status, stop=self.stop))

    def request_stop(self):
        self.stop.set()
        self.stop_btn.configure(state="disabled")
        self.log("Stopping…")

    def on_close(self):
        if self.busy():
            if not messagebox.askyesno(
                    "ffdl", "A job is still running. Stop it and quit?\n"
                            "Unfinished downloads resume next time."):
                return
            self.stop.set()
            self.closing = True
            self.root.withdraw()
            self.root.after(15_000, self.root.destroy)  # don't wait forever
        else:
            self.root.destroy()

    # ---------- worker thread ----------

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def run_job(self, kind, fn):
        def target():
            try:
                self.events.put(("finished", kind, fn(), None))
            except Exception as e:
                self.events.put(("finished", kind, None, e))

        self.set_busy(True, kind)
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    # These run on the worker thread: they only queue events for the UI.
    def log(self, msg):
        self.events.put(("log", msg))

    def on_progress(self, done, total, speed, elapsed):
        self.events.put(("progress", done, total, speed, elapsed))

    def on_status(self, url, state):
        self.events.put(("status", url, state))

    # ---------- UI thread ----------

    def poll(self):
        try:
            while True:
                self.handle(*self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def handle(self, kind, *data):
        if kind == "log":
            self.log_box.configure(state="normal")
            self.log_box.insert("end", data[0] + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        elif kind == "progress":
            done, total, speed, elapsed = data
            self.bar["value"] = done * 100 / total if total else 0
            self.stats_var.set(f"{ffdl.fmt_size(done)} / {ffdl.fmt_size(total)}    "
                               f"{ffdl.fmt_size(speed)}/s    {ffdl.fmt_time(elapsed)}    "
                               f"{self.finished}/{len(self.job_links)} files done")
        elif kind == "status":
            url, state = data
            item = self.item_of.get(url)
            if item:
                self.tree.set(item, "status", STATUS_TEXT.get(state, state))
            if state == "resolving":
                self.tree.see(item)
                n = self.job_links.index(url) + 1
                self.current_var.set(f"[{n}/{len(self.job_links)}] {ffdl.name_from_url(url) or url}")
                self.bar["value"] = 0
                self.stats_var.set(f"getting the download link…    "
                                   f"{self.finished}/{len(self.job_links)} files done")
            elif state in ("done", "skipped"):
                self.finished += 1
        elif kind == "finished":
            job, result, error = data
            self.set_busy(False)
            if self.closing:
                self.root.destroy()
                return
            if error:
                self.log(f"Error: {error}")
                messagebox.showerror("ffdl", str(error))
            elif job == "find":
                if result:
                    self.fill_tree(result)
                    self.log(f"Found {len(result)} link(s).")
                else:
                    messagebox.showinfo("ffdl", "No fuckingfast.co links found.")
            elif job == "download":
                self.current_var.set("")
                self.stats_var.set(f"{self.finished}/{len(self.job_links)} files done"
                                   + (f", {len(result)} failed" if result else ""))

    def set_busy(self, busy, kind=None):
        state = "disabled" if busy else "normal"
        for btn in (self.paste_btn, self.find_btn, self.browse_btn, self.download_btn):
            btn.configure(state=state)
        self.stop_btn.configure(state="normal" if busy and kind == "download" else "disabled")
        if busy and kind == "find":
            self.current_var.set("Looking for links…")
        elif not busy:
            self.current_var.set("")


def main():
    root = tk.Tk()
    app = App(root)
    if sys.argv[1:]:  # ffdl links-or-page-URL: fill the box and look them up
        app.input.insert("1.0", "\n".join(sys.argv[1:]))
        root.after(300, app.find_links)
    root.mainloop()


if __name__ == "__main__":
    main()
