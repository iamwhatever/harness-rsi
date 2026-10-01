"""The `dom_assert` check: open a built SPA in headless Chromium and assert one element's text.

The SPA under `root` (relative to the workdir, default ".") is served on loopback with index.html
fallback; nothing under /api is served, so an exam names every API answer it needs as a `route`
setup step. Returns (ok, detail); ok is None when the check cannot run (no Playwright, no browser,
no built SPA), so it is never mistaken for a failed assertion.
"""

import functools
import http.server
import json
import os
import re
import threading
import time
import urllib.parse


class _Spa(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_head(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/"):
            self.send_error(404, "no gateway: stub this path with a route step")
            return None
        local = self.translate_path(path)  # contained in the served dir by SimpleHTTPRequestHandler
        if path == "/" or not os.path.isfile(local):
            self.path = "/index.html"
        return super().send_head()


def _match(routes, path):
    for r in routes:
        want = r["route"]
        if path == want or (want.endswith("*") and path.startswith(want[:-1])):
            return r
    return None


def _norm(text):
    return " ".join(text.split())


def run(check, ctx):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return None, "playwright not installed (pip install playwright)"
    root = (ctx["workdir"] / check.get("root", ".")).resolve()
    if not (root / "index.html").is_file():
        return None, f"no built SPA: {check.get('root', '.')}/index.html missing"
    setup = check.get("setup", [])
    routes = [s for s in setup if "route" in s]
    storage = {k: v for s in setup if "storage" in s for k, v in s["storage"].items()}
    timeout = check.get("timeout_s", 30) * 1000
    want = (lambda t: re.search(check["regex"], t) is not None) if "regex" in check else (lambda t: _norm(check["text"]) in t)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Spa, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Error as exc:
                return None, f"cannot launch browser: {str(exc).splitlines()[0]}"
            page = browser.new_page(viewport={"width": 1400, "height": 900}, locale="en-US")
            page.route_web_socket(re.compile(r"/api/"), lambda ws: None)

            def answer(route):
                hit = _match(routes, urllib.parse.urlsplit(route.request.url).path)
                if hit is None:
                    return route.continue_()
                body = json.dumps(hit.get("json"))
                return route.fulfill(status=hit.get("status", 200), content_type="application/json", body=body)

            page.route("**/api/**", answer)
            page.add_init_script(f"for (const [k, v] of Object.entries({json.dumps(storage)})) localStorage.setItem(k, v)")
            page.set_default_timeout(timeout)
            try:
                page.goto(f"http://127.0.0.1:{srv.server_port}{check['path']}")
                for step in setup:
                    if "click" in step:
                        page.locator(step["click"]).first.click()
                    elif "fill" in step:
                        page.locator(step["fill"][0]).first.fill(step["fill"][1])
                    elif "wait_for" in step:
                        page.locator(step["wait_for"]).first.wait_for()
                el = page.locator(check["selector"]).first
                el.wait_for()
                deadline = time.monotonic() + timeout / 1000
                while not want(got := _norm(el.inner_text())) and time.monotonic() < deadline:
                    page.wait_for_timeout(250)  # the text may settle after more reads land
            except Error as exc:
                return False, f"{check['selector']}: {str(exc).splitlines()[0]}"
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    expected = f"/{check['regex']}/" if "regex" in check else repr(check["text"])
    return want(got), f"{check['selector']} text {got!r} vs {expected}"
