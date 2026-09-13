"""Optional browser provider. Works with a supplied context or an owned ephemeral one.

No storage_state, cookies, credentials or browser endpoints are serialized.
An authenticated context can be injected by another trusted runtime.
"""
from pathlib import Path
import time

from ..domain import CaptureError, AccessRequired, CaptureResult, BudgetExceeded
from .contract import WebResponse
from .local import check_url


class PlaywrightProvider:
    def __init__(self, context=None, *, engine="chromium", headless=False):
        self.owned = context is None
        if self.owned:
            from playwright.sync_api import sync_playwright
            self.driver = sync_playwright().start()
            try:
                self.browser = getattr(self.driver, engine).launch(headless=headless)
            except Exception:
                self.driver.stop()
                raise CaptureError("Browser provider could not launch. Check browser installation and runtime permissions; filesystem and HTTP remain available.") from None
            context = self.browser.new_context(accept_downloads=True)
        self.context = context
        self.page = context.new_page()

    def capabilities(self):
        return {"inspect": True, "snapshot": True, "download": True, "authenticated_context": "runtime-owned",
                "limitations": ["Rendered snapshots are observed representations", "Browser network bytes are not fully measurable before loading"]}

    def inspect(self, location, scope, max_bytes, max_actions):
        check_url(location, scope, asset=True)
        requests = 0
        blocked = []
        def guard(route):
            nonlocal requests
            requests += 1
            try:
                check_url(route.request.url, scope, asset=True)
                if requests > max_actions:
                    blocked.append("request budget")
                    route.abort()
                elif route.request.resource_type in ("image", "media", "font"):
                    route.abort()  # Discovery inventories these references without capturing bytes.
                else:
                    route.continue_()
            except CaptureError:
                blocked.append("out-of-scope browser request")
                route.abort()
        self.page.route("**/*", guard)
        actions = 0
        try:
            response = self.page.goto(location, wait_until="domcontentloaded", timeout=30000)
            if response and response.status in (401, 403) or self.page.locator('input[type="password"]').count():
                raise AccessRequired("Sign in in the browser window, then resume discovery. Browser access remains runtime-local.")
            previous, stable = None, 0
            while actions + requests < max_actions and stable < 2:
                expanded = False
                controls = self.page.locator('button[aria-expanded="false"], [role="button"][aria-expanded="false"], details:not([open]) > summary')
                for index in range(controls.count()):
                    control = controls.nth(index)
                    if control.is_visible():
                        control.click(timeout=3000)
                        expanded = True
                        actions += 1
                        break
                if not expanded:
                    # Generic navigation controls, never arbitrary page instructions.
                    more = self.page.get_by_role("button", name=__import__("re").compile(r"^(load|show)\s+.*more(\s+content.*|\s+items.*)?$|^load more$", __import__("re").I))
                    if more.count() and more.first.is_visible():
                        more.first.click(timeout=3000)
                        actions += 1
                        expanded = True
                self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                actions += 1
                self.page.wait_for_timeout(100)
                signature = self.page.locator("body").inner_text(timeout=3000)
                stable = stable + 1 if signature == previous and not expanded else 0
                previous = signature
            html = self.page.content()
            # Open shadow roots are flattened into evidence fragments. Inaccessible frames remain references.
            shadow = self.page.evaluate("""() => {const out=[]; const visit=(root)=>{for(const el of root.querySelectorAll('*')){
                if(el.shadowRoot){out.push(el.shadowRoot.innerHTML);visit(el.shadowRoot)}}}; visit(document); return out.join('\\n')}""")
            if shadow:
                html += "\n" + shadow
            body = html.encode()
            return WebResponse(body[:max_bytes], self.page.url,
                {"content-type": "text/html"}, complete=stable >= 2 and len(body) <= max_bytes and not blocked,
                limitations=["Rendered browser representation; images/media were not downloaded during discovery", *blocked],
                actions=max(1, requests + actions), method="browser")
        except AccessRequired:
            raise
        except Exception:
            raise CaptureError("Browser inspection failed or timed out; runtime access may need recovery.") from None
        finally:
            self.page.unroute("**/*", guard)

    def capture(self, location, scope, destination, max_bytes, timeout, method):
        check_url(location, scope, asset=True)
        if method == "browser-snapshot":
            response = self.inspect(location, scope, max_bytes, 100)
            if not response.complete:
                raise CaptureError("Rendered snapshot is incomplete; preserve discovery findings and review alternatives.")
            destination.write_bytes(response.body)
            return CaptureResult(destination, "text/html", role="snapshot", limitations=response.limitations)
        if method == "browser-download":
            # Request context shares authentication in memory. Validate every redirect ourselves.
            current = location
            response = None
            for _ in range(6):
                check_url(current, scope, asset=True)
                response = self.context.request.get(current, max_redirects=0, timeout=timeout * 1000)
                if response.status in (401, 403):
                    raise AccessRequired("Browser authentication is required for this representation.")
                if response.status in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin
                    current = urljoin(current, response.headers.get("location", ""))
                    response.dispose()
                    continue
                break
            if response is None or not response.ok:
                raise CaptureError("Authenticated browser retrieval failed.")
            try:
                if int(response.headers.get("content-length", 0)) > max_bytes:
                    raise BudgetExceeded("Browser response exceeds the approved byte budget.")
                body = response.body()
                if len(body) > max_bytes:
                    raise BudgetExceeded("Browser response exceeds the approved byte budget.")
                destination.write_bytes(body)
                return CaptureResult(destination, response.headers.get("content-type", "application/octet-stream").split(";")[0],
                                     limitations=["Browser provider buffers responses; transfer byte limits may be detected after receipt"])
            finally:
                response.dispose()
        raise CaptureError("Unsupported browser capture method.")

    def close(self):
        self.page.close()
        if self.owned:
            self.context.close()
            self.browser.close()
            self.driver.stop()
