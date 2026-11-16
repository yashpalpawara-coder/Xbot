from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.grok_find import LIMIT_NOTICE, limit_reached, limit_reply
from app.writer import is_preamble
from app.x_account import same_account


def prompt_is_present(have: str, prompt: str) -> bool:
    """True when the composer holds the prompt, even if blank lines were inserted."""
    got = " ".join((have or "").split())
    want = " ".join((prompt or "").split())
    if len(want) < 12:
        return False
    return want[:60] in got


def composer_rank(selector: str, area: float, text: str) -> float:
    """Prefer the large chat composer over a tiny or hidden Ask box."""
    score = area
    if selector.startswith("#prompt") or "ProseMirror" in selector:
        score += 5_000
    if "Analyze this product" in text or len(" ".join(text.split())) > 40:
        score += 50_000
    return score


def x_login_refusal(payload: str) -> str:
    lowered = payload.lower()
    if "temporarily limited your login" in lowered:
        return "X refused the sign-in: We’ve temporarily limited your login. Please try again later."
    if "not allowed to log in" in lowered:
        return "X refused the sign-in: Sorry, you are not allowed to log in at this time."
    return ""


_TOOL_LINE = re.compile(r"^(Searched \d+ websites|Tested .+)$", re.I)
_STATUS_LINE = re.compile(r"^(Thinking|Searching|Reasoning)(\.{3})?$", re.I)


def _chatgpt_conversation(url: str) -> str:
    """The ChatGPT chat id. A different id is a different chat."""
    path = urlsplit(url or "").path
    marker = "/c/"
    if marker not in path:
        return ""
    return path.split(marker, 1)[1].split("/")[0]


def _grok_conversation(url: str) -> str:
    """The X Grok chat id. It lives in ?conversation= or /conversation/."""
    parsed = urlsplit(url or "")
    query = parse_qs(parsed.query)
    if query.get("conversation"):
        return query["conversation"][0]
    marker = "/conversation/"
    if marker in parsed.path:
        return parsed.path.split(marker, 1)[1].split("/")[0]
    return ""

_remote_lock = threading.Lock()
_remote_png = b""
_remote_actions: list[dict[str, object]] = []


def remote_screen() -> bytes:
    with _remote_lock:
        return _remote_png


def remote_click(x: int, y: int) -> None:
    with _remote_lock:
        _remote_actions.append({"click": (int(x), int(y))})


def remote_type(text: str) -> None:
    with _remote_lock:
        _remote_actions.append({"type": text[:200]})


def remote_key(key: str) -> None:
    if key not in {"Enter", "Backspace", "Tab"}:
        return
    with _remote_lock:
        _remote_actions.append({"key": key})


def _clear_remote() -> None:
    with _remote_lock:
        global _remote_png
        _remote_png = b""
        _remote_actions.clear()


EXTRACT_JS = """
() => {
  function parseCount(raw) {
    if (raw == null) return null;
    const text = String(raw).replace(/,/g, " ").trim();
    const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*([KMB])?/i);
    if (!match) return null;
    let value = Number(match[1]);
    const unit = (match[2] || "").toUpperCase();
    if (unit === "K") value *= 1000;
    if (unit === "M") value *= 1000000;
    if (unit === "B") value *= 1000000000;
    return Number.isFinite(value) ? Math.round(value) : null;
  }
  function metric(article, testid) {
    const node = article.querySelector(`[data-testid="${testid}"]`);
    if (!node) return null;
    const label = `${node.getAttribute("aria-label") || ""} ${node.innerText || ""}`;
    const parsed = parseCount(label);
    return parsed === null ? 0 : parsed;
  }
  function views(article) {
    const labelled = article.querySelector('[aria-label*="view" i], a[href*="/analytics"]');
    if (!labelled) return null;
    return parseCount(labelled.getAttribute("aria-label") || labelled.innerText || "");
  }
  const posts = [];
  for (const article of document.querySelectorAll('article[data-testid="tweet"]')) {
    if (article.parentElement && article.parentElement.closest('article[data-testid="tweet"]')) continue;
    const text = (article.querySelector('[data-testid="tweetText"]')?.innerText || "").trim();
    const userBlock = article.querySelector('[data-testid="User-Name"]');
    const handleHref = userBlock?.querySelector('a[href^="/"]')?.getAttribute("href") || "";
    const handle = (handleHref.split("/").filter(Boolean)[0] || "").replace(/^@/, "");
    const name = ((userBlock?.innerText || "").split("\\n")[0] || handle).trim();
    const hrefs = [...article.querySelectorAll('a[href*="/status/"]')].map((node) => node.getAttribute("href") || "");
    const href = hrefs.find((value) => value.includes("/" + handle + "/status/")) || hrefs[0] || "";
    const match = href.match(/status\\/(\\d+)/);
    if (!match || !text) continue;
    const path = href.startsWith("http") ? href : "https://x.com" + href.split("?")[0];
    posts.push({
      external_id: match[1],
      author: name,
      handle,
      text,
      url: path,
      views: views(article),
      likes: metric(article, "like") ?? metric(article, "unlike"),
      reposts: metric(article, "retweet") ?? metric(article, "unretweet"),
      replies: metric(article, "reply"),
      created_at: article.querySelector("time")?.getAttribute("datetime") || null
    });
  }
  return posts;
}
"""


@dataclass
class SessionState:
    platform: str
    status: str
    handle: str = ""
    detail: str = ""


def _chromium_ready(path: Path) -> bool:
    return any(path.glob("chromium-*/chrome-linux*/chrome"))


def running_on_server() -> bool:
    """Cloud Run sets K_SERVICE; Render sets RENDER=true."""
    return os.environ.get("RENDER") == "true" or bool(os.environ.get("K_SERVICE"))


class BrowserSession:
    """One persistent Chrome profile. The caller holds the process-wide browser lock."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.chatgpt_url = ""
        self.grok_url = ""

    def _install_server_chromium(self) -> None:
        """Render's build cache is empty at runtime, so Chromium has to live in the app directory."""
        configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if configured and configured != "0" and _chromium_ready(Path(configured)):
            return
        target = self.settings.profile_path.parent / "ms-playwright"
        target.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(target)
        if _chromium_ready(target):
            return
        logging.getLogger(__name__).info("Downloading Chromium into %s", target)
        completed = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            timeout=600,
        )
        if completed.returncode != 0 or not _chromium_ready(target):
            raise RuntimeError("Chromium could not be downloaded on the server. The browser files are still missing.")

    def __enter__(self) -> BrowserSession:
        on_server = running_on_server()
        if on_server:
            self._install_server_chromium()
        self.settings.profile_path.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        options: dict[str, Any] = {
            "user_data_dir": str(self.settings.profile_path),
            "headless": True if on_server else self.settings.headless,
            "viewport": {"width": 1360, "height": 900},
            "locale": "en-US",
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            if on_server:
                options["args"] = [*options["args"], "--no-sandbox", "--disable-dev-shm-usage"]
                self.context = self._playwright.chromium.launch_persistent_context(**options)
            else:
                try:
                    self.context = self._playwright.chromium.launch_persistent_context(channel="chrome", **options)
                except Exception:
                    options["args"] = [
                        *options["args"],
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ]
                    self.context = self._playwright.chromium.launch_persistent_context(**options)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    def _sync_remote(self, page: Page) -> None:
        """Apply clicks typed in the dashboard, then publish what the server browser shows."""
        with _remote_lock:
            actions = list(_remote_actions)
            _remote_actions.clear()
        for action in actions:
            try:
                if "click" in action:
                    x, y = action["click"]
                    page.mouse.click(x, y)
                elif "type" in action:
                    page.keyboard.type(str(action["type"]))
                elif "key" in action:
                    page.keyboard.press(str(action["key"]))
            except Exception:
                logging.getLogger(__name__).info("remote browser action failed")
        try:
            png = page.screenshot(type="png")
        except Exception:
            return
        with _remote_lock:
            global _remote_png
            _remote_png = png

    def __exit__(self, *_: object) -> None:
        if self.context is not None:
            self.context.close()
        _clear_remote()
        if self._playwright is not None:
            self._playwright.stop()

    def _page(self, needle: str) -> Page:
        assert self.context is not None
        for page in self.context.pages:
            if needle in (page.url or ""):
                return page
        page = self.context.pages[0] if self.context.pages else self.context.new_page()
        if page.url in {"", "about:blank"}:
            return page
        return self.context.new_page()

    def open_x(self) -> Page:
        page = self._page("x.com")
        page.goto(self.settings.x_home_url, wait_until="domcontentloaded", timeout=45_000)
        self._dismiss_cookies(page)
        return page

    def open_chatgpt(self) -> Page:
        page = self._page("chatgpt.com")
        page.goto(self.settings.chatgpt_home_url, wait_until="domcontentloaded", timeout=45_000)
        return page

    def open_grok(self) -> Page:
        """Grok inside the signed-in X account. This does not open grok.com."""
        page = self._page("x.com")
        page.goto(self.settings.grok_home_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        self._dismiss_cookies(page)
        if not self.x_logged_in(page):
            page = self.sign_in_if_needed(page)
            page.goto(self.settings.grok_home_url, wait_until="domcontentloaded", timeout=45_000)
            if not self.x_logged_in(page):
                raise RuntimeError("X is signed out. Add the username and password in Settings.")
        return page

    def x_logged_in(self, page: Page) -> bool:
        if "/login" in page.url or "/i/flow/login" in page.url:
            return False
        locator = page.locator(
            '[data-testid="AppTabBar_Profile_Link"], [data-testid="SideNav_AccountSwitcher_Button"]'
        )
        try:
            locator.first.wait_for(state="visible", timeout=8_000)
        except PlaywrightTimeout:
            return False
        return True

    def x_handle(self, page: Page) -> str:
        link = page.locator('[data-testid="AppTabBar_Profile_Link"]').first
        if link.count() == 0:
            return ""
        href = link.get_attribute("href") or ""
        parts = [part for part in href.split("/") if part]
        return parts[0] if parts else ""

    def use_x_account(
        self,
        username: str,
        password: str,
        handle: str,
        wait: Callable[[], bool] | None = None,
        notice: Callable[[str], None] | None = None,
    ) -> SessionState:
        """Sign out of whoever is open, then sign in as the product that was selected."""
        page = self.open_x()
        logged_in = self.x_logged_in(page)
        current = self.x_handle(page) if logged_in else ""
        if logged_in and same_account(current, username, handle):
            message = f"Already signed in as @{current}."
            if notice:
                notice(message)
            return SessionState("x", "signed_in", current, message)
        if logged_in:
            who = f"@{current}" if current else "the open account"
            if notice:
                notice(f"Signing out of {who}.")
            self.sign_out_x(page)
            page = self.open_x()
            if self.x_logged_in(page):
                still = self.x_handle(page)
                if same_account(still, username, handle):
                    message = f"Already signed in as @{still}."
                    if notice:
                        notice(message)
                    return SessionState("x", "signed_in", still, message)
                return SessionState(
                    "x",
                    "needs_manual",
                    still or current,
                    f"Could not sign out of {who}. Sign out in the Chrome window, then select the product again.",
                )
        if not str(username or "").strip() or not str(password or ""):
            return SessionState(
                "x",
                "signed_out",
                "",
                "This product has no X login. Add the username and password in Settings, then select it again.",
            )
        label = str(handle or username).strip().lstrip("@")
        if notice:
            notice(f"Signing in as {label}.")
        self.settings.x_username = str(username).strip()
        self.settings.x_password = password
        self.settings.x_handle = str(handle or "").strip().lstrip("@") or (
            str(username).strip() if "@" not in str(username) else ""
        )
        return self.ensure_x(wait, notice)

    def sign_out_x(self, page: Page) -> None:
        """Leave the account that is currently open in this Chrome profile."""
        if "/login" in (page.url or "") or "/i/flow/login" in (page.url or ""):
            return
        try:
            switcher = page.locator('[data-testid="SideNav_AccountSwitcher_Button"]')
            switcher.first.wait_for(state="visible", timeout=8_000)
            switcher.first.click()
            page.wait_for_timeout(500)
            logout = page.locator('[data-testid="AccountSwitcher_Logout_Button"]')
            if logout.count() == 0:
                logout = page.get_by_role("menuitem", name=re.compile(r"log out", re.I))
            if logout.count() == 0:
                logout = page.get_by_text(re.compile(r"^log out", re.I))
            logout.first.click()
            page.wait_for_timeout(500)
            confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
            if confirm.count() and confirm.first.is_visible():
                confirm.first.click()
            else:
                button = page.get_by_role("button", name=re.compile(r"^log out$", re.I))
                if button.count() and button.first.is_visible():
                    button.first.click()
        except Exception:
            logging.getLogger(__name__).info("X menu logout did not finish")
        if self._x_still_open(page):
            try:
                page.goto("https://x.com/logout", wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(800)
                button = page.locator('[data-testid="confirmationSheetConfirm"]')
                if button.count() == 0:
                    button = page.get_by_role("button", name=re.compile(r"log out", re.I))
                if button.count() and button.first.is_visible():
                    button.first.click()
            except Exception:
                logging.getLogger(__name__).info("X logout page did not finish")
        deadline = time.time() + 12
        while time.time() < deadline and self._x_still_open(page):
            page.wait_for_timeout(400)

    def _x_still_open(self, page: Page) -> bool:
        url = page.url or ""
        if "/login" in url or "/i/flow/login" in url or url.rstrip("/").endswith("/logout"):
            return False
        locator = page.locator('[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"]')
        try:
            return locator.count() > 0 and locator.first.is_visible()
        except Exception:
            return False

    def ensure_x(self, wait: Callable[[], bool] | None = None, notice: Callable[[str], None] | None = None) -> SessionState:
        page = self.open_x()
        if self.x_logged_in(page):
            return SessionState("x", "signed_in", self.x_handle(page), "X session is signed in.")
        username = self.settings.x_username.strip()
        password = self.settings.x_password
        if not username or not password:
            return SessionState("x", "signed_out", "", "X is signed out. Add the username and password in Settings.")
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=45_000)
        try:
            self._submit_x_login(page, username, password)
        except RuntimeError as exc:
            return SessionState("x", "needs_manual", "", str(exc))
        except PlaywrightTimeout:
            return self._wait_x(page, wait, notice)
        page.wait_for_timeout(2000)
        if self.x_logged_in(page):
            return SessionState("x", "signed_in", self.x_handle(page), "Signed in to X.")
        return self._wait_x(page, wait, notice)

    def sign_in_if_needed(self, page: Page, wait: Callable[[], bool] | None = None) -> Page:
        """Log in with the saved X username and password when this window is signed out."""
        if self.x_logged_in(page):
            return page
        state = self.ensure_x(wait)
        if state.status != "signed_in":
            raise RuntimeError(state.detail)
        return self._page("x.com")

    def _submit_x_login(self, page: Page, username: str, password: str) -> None:
        """Same order as xBot-clean: username, password switch, then the identity check."""
        page.wait_for_timeout(1500)
        self._dismiss_cookies(page)
        username_box = page.locator(
            'input[name="text"], input[autocomplete="username"], input[name="username_or_email"]'
        ).first
        username_box.wait_for(state="visible", timeout=20_000)
        username_box.click()
        username_box.fill(username)
        refusal = self._submit_login_action(page)
        if refusal:
            raise RuntimeError(refusal)
        page.wait_for_timeout(1500)
        refusal = self._choose_password(page)
        if refusal:
            raise RuntimeError(refusal)
        if not self._password_visible(page):
            hint = self.settings.x_handle.strip().lstrip("@") or username
            refusal = self._answer_identity_check(page, hint)
            if refusal:
                raise RuntimeError(refusal)
            refusal = self._choose_password(page)
            if refusal:
                raise RuntimeError(refusal)
        if not self._password_visible(page):
            snippet = self._page_snippet(page)
            detail = f" X showed: {snippet}" if snippet else ""
            raise RuntimeError(f"X did not show the password field.{detail}")
        password_box = page.locator('input[name="password"], input[type="password"]').first
        password_box.click()
        password_box.fill(password)
        refusal = self._submit_login_action(page)
        if refusal:
            raise RuntimeError(refusal)

    def _submit_login_action(self, page: Page) -> str:
        try:
            with page.expect_response(lambda response: "/onboarding/web/actions/" in response.url, timeout=12_000) as caught:
                self._click_login_control(page)
            return x_login_refusal(caught.value.text())
        except PlaywrightTimeout:
            return ""

    def _click_login_control(self, page: Page) -> None:
        for testid in ("LoginForm_Login_Button", "ocfEnterTextNextButton"):
            button = page.locator(f'[data-testid="{testid}"]')
            if button.count() and button.first.is_visible():
                button.first.click()
                return
        for label in ("Log in", "Continue", "Next"):
            button = page.get_by_role("button", name=label, exact=True)
            if button.count() and button.first.is_visible():
                button.first.click()
                return
            text = page.get_by_text(label, exact=True)
            for index in range(text.count()):
                item = text.nth(index)
                if item.is_visible():
                    item.click()
                    return
        submit = page.locator('button[type="submit"]')
        if submit.count() and submit.last.is_visible():
            submit.last.click()
            return
        raise PlaywrightTimeout("X did not show a login button.")

    def _choose_password(self, page: Page) -> str:
        if self._password_visible(page):
            return ""
        switch = page.get_by_role("button", name="Use password").or_(page.get_by_role("link", name="Use password"))
        if not (switch.count() and switch.first.is_visible()):
            switch = self._password_switch(page)
        if switch is None or switch.count() == 0 or not switch.first.is_visible():
            return ""
        try:
            with page.expect_response(lambda response: "/onboarding/web/actions/" in response.url, timeout=12_000) as caught:
                switch.first.click()
            return x_login_refusal(caught.value.text())
        except PlaywrightTimeout:
            page.wait_for_timeout(1200)
            return ""

    def _password_switch(self, page: Page) -> Any | None:
        candidates: list[Any] = []
        for role in ("button", "link", "tab"):
            matches = page.get_by_role(role, name=re.compile(r"^password$", re.I))
            for index in range(matches.count()):
                item = matches.nth(index)
                if item.is_visible():
                    candidates.append(item)
        if not candidates:
            return None
        best = None
        best_score = None
        for item in candidates:
            box = item.bounding_box()
            if box is None:
                continue
            score = box["x"] - box["y"]
            if best_score is None or score > best_score:
                best = item
                best_score = score
        return best

    def _answer_identity_check(self, page: Page, hint: str) -> str:
        challenge = page.locator('input[data-testid="ocfEnterTextTextInput"], input[name="challenge_response"]')
        try:
            challenge.first.wait_for(state="visible", timeout=4_000)
        except PlaywrightTimeout:
            return ""
        try:
            body = page.locator("body").inner_text(timeout=2_000).lower()
        except PlaywrightTimeout:
            body = ""
        if "code" in body:
            return ""
        answer = hint.strip()
        if not answer:
            return ""
        challenge.first.fill(answer)
        return self._submit_login_action(page)

    def _page_snippet(self, page: Page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=3_000)
        except PlaywrightTimeout:
            return ""
        return " ".join(text.split())[:240]

    def _submit_jf_login(self, page: Page, username: str, password: str) -> None:
        email = page.locator('input[name="username_or_email"]').first
        email.click()
        email.fill(username)
        refusal = self._submit_jf_action(page)
        if refusal:
            raise RuntimeError(refusal)
        challenge = page.locator('input[name="challenge_response"]')
        try:
            challenge.first.wait_for(state="visible", timeout=12_000)
        except PlaywrightTimeout:
            challenge = None
        if challenge is not None and self._named_input_visible(page, "challenge_response"):
            handle = self.settings.x_handle.strip().lstrip("@")
            if not handle:
                raise RuntimeError("X is asking for the username on this account. Add X_HANDLE to backend/.env.")
            challenge.first.click()
            challenge.first.fill(handle)
            refusal = self._submit_jf_action(page)
            if refusal:
                raise RuntimeError(refusal)
        if not self._jf_password_ready(page):
            raise RuntimeError("X did not open the password step after confirming the account.")
        password_box = page.locator('input[name="password"]').first
        password_box.click()
        password_box.fill(password)
        refusal = self._submit_jf_action(page)
        if refusal:
            raise RuntimeError(refusal)

    def _submit_jf_action(self, page: Page) -> str:
        try:
            with page.expect_response(lambda response: "/onboarding/web/actions/" in response.url, timeout=12_000) as caught:
                page.locator('button[type="submit"]').last.click()
            payload = caught.value.text()
        except PlaywrightTimeout:
            return ""
        return x_login_refusal(payload)

    def _jf_password_ready(self, page: Page, seconds: float = 12) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            ready = page.evaluate(
                """() => {
                  const el = document.querySelector('input[name="password"]');
                  if (!el) return false;
                  const style = getComputedStyle(el);
                  return style.opacity !== '0' && el.getBoundingClientRect().height > 0;
                }"""
            )
            if ready:
                return True
            page.wait_for_timeout(250)
        return False

    def _named_input_visible(self, page: Page, name: str) -> bool:
        box = page.locator(f'input[name="{name}"]')
        try:
            return box.count() > 0 and box.first.is_visible()
        except PlaywrightTimeout:
            return False

    def _confirm_x_account(self, page: Page) -> None:
        """Email sign-in often asks for the @handle before the password."""
        deadline = time.time() + 12
        while time.time() < deadline:
            if self._password_visible(page):
                return
            if self._x_account_check_visible(page):
                handle = self.settings.x_handle.strip().lstrip("@")
                if handle:
                    field = page.locator('input[data-testid="ocfEnterTextTextInput"], input[name="text"]').last
                    field.wait_for(state="visible", timeout=5_000)
                    field.fill(handle)
                    self._click_login_advance(page)
                else:
                    self._click_use_password(page)
                return
            page.wait_for_timeout(250)

    def _password_visible(self, page: Page) -> bool:
        box = page.locator('input[name="password"], input[type="password"]')
        try:
            return box.count() > 0 and box.first.is_visible()
        except PlaywrightTimeout:
            return False

    def _x_account_check_visible(self, page: Page) -> bool:
        for label in ("Confirm your account", "Enter your phone number or username"):
            heading = page.get_by_text(label, exact=False)
            try:
                if heading.count() and heading.first.is_visible():
                    return True
            except PlaywrightTimeout:
                return False
        return False

    def _click_login_advance(self, page: Page) -> None:
        for name in ("Continue", "Next"):
            button = page.get_by_role("button", name=name)
            try:
                if button.count() and button.first.is_visible():
                    button.first.click()
                    return
            except PlaywrightTimeout:
                return
        fallback = page.locator('[data-testid="ocfEnterTextNextButton"]')
        if fallback.count():
            fallback.first.click()

    def _click_use_password(self, page: Page) -> None:
        control = page.get_by_role("button", name="Use password").or_(page.get_by_role("link", name="Use password"))
        try:
            if control.count() and control.first.is_visible():
                control.first.click()
        except PlaywrightTimeout:
            return

    def _wait_x(self, page: Page, wait: Callable[[], bool] | None, notice: Callable[[str], None] | None = None) -> SessionState:
        """Leave Chrome open while X shows a code, captcha, or other extra check."""
        try:
            page.bring_to_front()
        except Exception:
            pass
        if notice:
            notice("Finish the extra check in the Chrome window. It stays open.")

        def signed_in() -> bool:
            try:
                if page.is_closed():
                    return False
                return self.x_logged_in(page)
            except Exception:
                return False

        if self._wait_until(signed_in, wait, 480, tick=lambda: self._sync_remote(page)):
            return SessionState("x", "signed_in", self.x_handle(page), "Signed in to X.")
        try:
            closed = page.is_closed()
        except Exception:
            closed = True
        if closed:
            detail = "The Chrome window closed before the extra check finished. Click Check X again and complete it while the window is open."
        else:
            detail = "The extra check was not finished. Click Check X again and complete it while the window is open."
        return SessionState("x", "needs_manual", "", detail)

    def chatgpt_logged_in(self, page: Page) -> bool:
        if self._chatgpt_signed_out(page):
            return False
        if self._chatgpt_composer(page) is not None:
            return True
        return self._chatgpt_shell(page)

    def _chatgpt_signed_out(self, page: Page) -> bool:
        """The logged-out home still shows New chat, so the login button is the real signal."""
        for name in ("Log in", "Sign up for free"):
            control = page.get_by_role("button", name=name).or_(page.get_by_role("link", name=name))
            try:
                if control.count() and control.first.is_visible():
                    return True
            except PlaywrightTimeout:
                return False
        return False

    def _chatgpt_shell(self, page: Page) -> bool:
        shell = page.get_by_role("link", name="New chat").or_(page.get_by_role("button", name="New chat"))
        try:
            return shell.count() > 0 and shell.first.is_visible()
        except PlaywrightTimeout:
            return False

    def _chatgpt_composer(self, page: Page):
        selectors = (
            "#prompt-textarea",
            "div.ProseMirror[role='textbox']",
            "div.ProseMirror[contenteditable='true']",
            "#mobile-composer-prompt",
            "textarea[placeholder='Ask ChatGPT']",
        )
        best = None
        best_score = -1.0
        for selector in selectors:
            boxes = page.locator(selector)
            try:
                count = min(boxes.count(), 6)
            except Exception:
                continue
            for index in range(count):
                box = boxes.nth(index)
                try:
                    if not box.is_visible(timeout=800):
                        continue
                    rect = box.bounding_box(timeout=800) or {}
                except Exception:
                    continue
                area = float(rect.get("width") or 0) * float(rect.get("height") or 0)
                if area < 40:
                    continue
                text = self._composer_text(box)
                score = composer_rank(selector, area, text)
                if score > best_score:
                    best = box
                    best_score = score
        if best is not None:
            return best
        named = page.get_by_role("textbox", name=re.compile(r"Ask ChatGPT|Ask anything|Message|Chat with ChatGPT|Where should we begin", re.I))
        try:
            if named.count() and named.first.is_visible(timeout=800):
                return named.first
        except Exception:
            return None
        return None

    def _composer_text(self, box) -> str:
        try:
            rendered = box.inner_text(timeout=1_500) or ""
        except Exception:
            rendered = ""
        if rendered.strip():
            return rendered
        try:
            value = box.input_value(timeout=1_500)
        except Exception:
            value = ""
        if str(value or "").strip():
            return str(value)
        try:
            value = box.evaluate("el => el.value || el.innerText || ''", timeout=1_500)
        except Exception:
            return rendered
        return str(value or rendered)

    def _write_chatgpt_prompt(self, page: Page, box, prompt: str) -> None:
        """Type into the composer. An empty ProseMirror ignores keys until it is focused."""
        needle = prompt[:24]
        last = ""
        for _ in range(3):
            current = self._chatgpt_composer(page) or box
            try:
                current.scroll_into_view_if_needed()
                current.click()
                current.evaluate("el => el.focus()")
            except PlaywrightTimeout:
                page.wait_for_timeout(400)
                continue
            page.wait_for_timeout(200)
            existing = self._composer_text(current).strip()
            if prompt_is_present(existing, prompt):
                return
            if existing:
                try:
                    current.evaluate(
                        """el => {
                          el.focus();
                          const selection = window.getSelection();
                          const range = document.createRange();
                          range.selectNodeContents(el);
                          selection.removeAllRanges();
                          selection.addRange(range);
                        }"""
                    )
                    page.keyboard.press("Backspace")
                except Exception:
                    try:
                        current.fill("")
                    except Exception:
                        pass
            page.keyboard.insert_text(prompt)
            self._notify_composer(current)
            if prompt_is_present(self._composer_text(current), prompt) or self._wait_for_composer_text(current, needle):
                return
            try:
                current.evaluate(
                    """(el, text) => {
                      el.focus();
                      const selection = window.getSelection();
                      const range = document.createRange();
                      range.selectNodeContents(el);
                      selection.removeAllRanges();
                      selection.addRange(range);
                      document.execCommand('insertText', false, text);
                    }""",
                    prompt,
                    timeout=2_000,
                )
            except Exception:
                pass
            self._notify_composer(current)
            if prompt_is_present(self._composer_text(current), prompt) or self._wait_for_composer_text(current, needle):
                return
            last = self._composer_text(current)[:80]
            page.wait_for_timeout(400)
        try:
            page.screenshot(path=str(self.settings.profile_path.parent / "chatgpt-last.png"), full_page=False)
        except PlaywrightTimeout:
            pass
        seen = f" The box showed: {last!r}." if last.strip() else ""
        raise RuntimeError(f"ChatGPT's message box stayed empty. Nothing was sent.{seen}")

    def _notify_composer(self, box) -> None:
        """Tell the page the text is really there, so Send can leave the disabled state."""
        try:
            box.evaluate(
                """el => {
                  const text = el.innerText || el.value || '';
                  el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
                }"""
            )
        except Exception:
            return

    def _click_chatgpt_send(self, page: Page, box) -> None:
        selector = (
            'button[data-testid="send-button"], button[data-testid="composer-submit-button"], '
            'button[aria-label="Send prompt"], button[aria-label="Send message"], button[aria-label="Send"]'
        )
        deadline = time.time() + 8
        nudged = False
        while time.time() < deadline:
            named = page.get_by_role("button", name=re.compile(r"^Send", re.I))
            buttons = page.locator(selector).or_(named)
            try:
                count = min(buttons.count(), 6)
            except Exception:
                count = 0
            for index in range(count):
                button = buttons.nth(index)
                try:
                    if button.is_visible(timeout=500) and not button.is_disabled(timeout=500):
                        button.click(timeout=2_000)
                        return
                except Exception:
                    continue
            if self._click_send_near_composer(page):
                return
            if not nudged:
                nudged = True
                try:
                    box.click()
                    page.keyboard.press("End")
                    page.keyboard.type(" ")
                    page.keyboard.press("Backspace")
                    self._notify_composer(box)
                except Exception:
                    pass
            page.wait_for_timeout(400)
        try:
            page.screenshot(path=str(self.settings.profile_path.parent / "chatgpt-last.png"), full_page=False)
        except Exception:
            pass
        raise RuntimeError("ChatGPT left Send disabled. Nothing was sent.")

    def _click_send_near_composer(self, page: Page) -> bool:
        """Click the enabled Send control that sits in the same card as the composer."""
        try:
            return bool(
                page.evaluate(
                    """() => {
                      const box = document.querySelector("#prompt-textarea, div.ProseMirror[role='textbox']");
                      if (!box) return false;
                      let node = box;
                      for (let depth = 0; depth < 8 && node; depth += 1) {
                        const send = [...node.querySelectorAll("button")].find((button) => {
                          const label = (button.getAttribute("aria-label") || "") + " " + (button.getAttribute("data-testid") || "");
                          return !button.disabled && /send/i.test(label);
                        });
                        if (send) {
                          send.click();
                          return true;
                        }
                        node = node.parentElement;
                      }
                      return false;
                    }"""
                )
            )
        except Exception:
            return False

    def _wait_for_composer_text(self, box, needle: str) -> bool:
        deadline = time.time() + 3
        while time.time() < deadline:
            if needle in self._composer_text(box):
                return True
            try:
                box.page.wait_for_timeout(200)
            except PlaywrightTimeout:
                return False
        return False

    def ensure_chatgpt(self, wait: Callable[[], bool] | None = None) -> SessionState:
        page = self.open_chatgpt()
        page.wait_for_timeout(1500)
        if self.chatgpt_logged_in(page):
            return SessionState("chatgpt", "signed_in", "", "ChatGPT session is signed in.")
        email = self.settings.chatgpt_email.strip()
        password = self.settings.chatgpt_password
        if not email or not password:
            return SessionState("chatgpt", "signed_out", "", "ChatGPT is signed out. Add the email and password, then check again.")
        try:
            self._submit_chatgpt_login(page, email, password)
        except PlaywrightTimeout:
            return self._wait_chatgpt(page, "Finish the ChatGPT sign-in prompt in the Chrome window.", wait)
        page.wait_for_timeout(2500)
        if self._challenge_visible(page):
            return self._wait_chatgpt(page, "ChatGPT is asking for a code or extra check. Finish it in the Chrome window.", wait)
        if self.chatgpt_logged_in(page):
            return SessionState("chatgpt", "signed_in", "", "Signed in to ChatGPT.")
        return self._wait_chatgpt(page, "ChatGPT did not finish signing in. Finish it in the Chrome window.", wait)

    def _submit_chatgpt_login(self, page: Page, email: str, password: str) -> None:
        login = page.get_by_role("button", name="Log in").or_(page.get_by_role("link", name="Log in"))
        if login.count() and login.first.is_visible():
            login.first.click()
            page.wait_for_timeout(1200)
        email_box = page.locator('input[type="email"], input[name="email"], input[name="username"]').first
        email_box.wait_for(state="visible", timeout=20_000)
        email_box.click()
        email_box.fill(email)
        self._click_auth_continue(page)
        if not self._password_visible(page):
            password_choice = page.get_by_role("button", name=re.compile(r"password", re.I)).or_(
                page.get_by_role("link", name=re.compile(r"password", re.I))
            )
            if password_choice.count() and password_choice.first.is_visible():
                password_choice.first.click()
                page.wait_for_timeout(800)
        password_box = page.locator('input[type="password"], input[name="password"]').first
        password_box.wait_for(state="visible", timeout=20_000)
        password_box.click()
        password_box.fill(password)
        self._click_auth_continue(page)

    def _click_auth_continue(self, page: Page) -> None:
        for name in ("Continue", "Log in", "Sign in"):
            button = page.get_by_role("button", name=name, exact=True)
            try:
                if button.count() and button.first.is_visible():
                    button.first.click()
                    page.wait_for_timeout(1200)
                    return
            except PlaywrightTimeout:
                return
        submit = page.locator('button[type="submit"]')
        if submit.count() and submit.first.is_visible():
            submit.first.click()
            page.wait_for_timeout(1200)

    def _wait_chatgpt(self, page: Page, detail: str, wait: Callable[[], bool] | None) -> SessionState:
        if not self._wait_until(lambda: self.chatgpt_logged_in(page), wait, tick=lambda: self._sync_remote(page)):
            return SessionState("chatgpt", "needs_manual", "", detail)
        return SessionState("chatgpt", "signed_in", "", "Signed in to ChatGPT.")

    def search_posts(self, query: str, limit: int, cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
        page = self.open_x()
        if not self.x_logged_in(page):
            state = self.ensure_x(cancelled)
            if state.status != "signed_in":
                raise RuntimeError(state.detail)
            page = self.open_x()
        url = "https://x.com/search?" + urlencode({"q": query, "src": "typed_query", "f": "live"})
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        if "/login" in page.url:
            raise RuntimeError("X signed out during search.")
        try:
            page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout:
            return []
        found: dict[str, dict[str, Any]] = {}
        stale = 0
        while len(found) < limit and stale < 3:
            if cancelled():
                break
            before = len(found)
            for item in page.evaluate(EXTRACT_JS):
                found.setdefault(str(item["external_id"]), item)
            if len(found) == before:
                stale += 1
            else:
                stale = 0
            if len(found) >= limit or cancelled():
                break
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(900)
        return list(found.values())[:limit]

    def read_status(self, url: str) -> dict[str, Any] | None:
        """Open one post directly. This does not load the X home page first."""
        page = self._page("x.com")
        target = url.split("?")[0]
        if target not in (page.url or ""):
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if not self.x_logged_in(page):
            page = self.sign_in_if_needed(page)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if not self.x_logged_in(page):
                raise RuntimeError("X is signed out. Add the username and password in Settings.")
        try:
            page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=8_000)
        except PlaywrightTimeout:
            return None
        posts = page.evaluate(EXTRACT_JS)
        return posts[0] if posts else None

    def read_discussion(self, url: str, limit: int = 40) -> dict[str, Any] | None:
        """Open one post and load the replies under it, with each reply's likes, views, and text."""
        page = self._page("x.com")
        target = url.split("?")[0]
        if target not in (page.url or ""):
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if not self.x_logged_in(page):
            page = self.sign_in_if_needed(page)
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if not self.x_logged_in(page):
                raise RuntimeError("X is signed out. Add the username and password in Settings.")
        try:
            page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=8_000)
        except PlaywrightTimeout:
            return None
        found: dict[str, dict[str, Any]] = {}
        stale = 0
        while stale < 3 and len(found) < limit + 1:
            before = len(found)
            for item in page.evaluate(EXTRACT_JS):
                found.setdefault(str(item["external_id"]), item)
            if len(found) == before:
                stale += 1
            else:
                stale = 0
            if len(found) >= limit + 1:
                break
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(700)
        posts = list(found.values())
        if not posts:
            return None
        match = re.search(r"status/(\d+)", url)
        target_id = match.group(1) if match else ""
        root = next((item for item in posts if str(item.get("external_id") or "") == target_id), posts[0])
        comments = [item for item in posts if str(item.get("external_id") or "") != str(root.get("external_id") or "")]
        opened = dict(root)
        opened["comments"] = comments[:limit]
        return opened

    def open_post(self, url: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        page = self.open_x()
        if not self.x_logged_in(page):
            state = self.ensure_x()
            if state.status != "signed_in":
                raise RuntimeError(state.detail)
            page = self.open_x()
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        posts = page.evaluate(EXTRACT_JS)
        if not posts:
            return None, []
        return posts[0], posts[1:7]

    def reply(self, status_url: str, text: str) -> str:
        """Send one reply. Returns published, uncertain, or raises before the click when nothing was sent."""
        page = self.open_x()
        if not self.x_logged_in(page):
            state = self.ensure_x()
            if state.status != "signed_in":
                raise RuntimeError(state.detail)
        page.goto(status_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        if self._reply_visible(page, text):
            return "published"
        if self._replies_limited(page):
            raise RuntimeError("The author limits who can reply to this post.")
        box = page.locator('[data-testid="tweetTextarea_0"]').first
        if not box.is_visible():
            opener = page.locator('article[data-testid="tweet"]').first.locator('[data-testid="reply"]')
            if opener.count() == 0:
                raise RuntimeError("X did not show a reply button.")
            opener.first.click()
            box.wait_for(state="visible", timeout=15_000)
        box.fill(text)
        button = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]').first
        button.wait_for(state="visible", timeout=10_000)
        if button.is_disabled():
            raise RuntimeError("X left the reply button disabled. Nothing was sent.")
        button.click()
        try:
            page.get_by_text(text[:80], exact=False).first.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout:
            if self._reply_visible(page, text):
                return "published"
            return "uncertain"
        return "published"

    def drop_reply(self, status_url: str, text: str) -> str:
        """Post one reply on this status, then like that same post. Does not load the X home page."""
        page = self._page("x.com")
        target = status_url.split("?")[0]
        if target not in (page.url or ""):
            page.goto(status_url, wait_until="domcontentloaded", timeout=30_000)
        if not self.x_logged_in(page):
            page = self.sign_in_if_needed(page)
            page.goto(status_url, wait_until="domcontentloaded", timeout=30_000)
            if not self.x_logged_in(page):
                raise RuntimeError("X is signed out. Add the username and password in Settings.")
        page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=12_000)
        if self._reply_visible(page, text) or self._already_replied(page):
            outcome = "published"
        else:
            if self._replies_limited(page):
                raise RuntimeError("The author limits who can reply to this post.")
            box = page.locator('[data-testid="tweetTextarea_0"]').first
            if not box.is_visible():
                opener = page.locator('article[data-testid="tweet"]').first.locator('[data-testid="reply"]')
                if opener.count() == 0:
                    raise RuntimeError("X did not show a reply button.")
                opener.first.click()
                box.wait_for(state="visible", timeout=15_000)
            box.fill(text)
            button = page.locator('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]').first
            button.wait_for(state="visible", timeout=10_000)
            if button.is_disabled():
                raise RuntimeError("X left the reply button disabled. Nothing was sent.")
            self._click_covered(button)
            try:
                page.get_by_text(text[:80], exact=False).first.wait_for(state="visible", timeout=15_000)
                outcome = "published"
            except PlaywrightTimeout:
                outcome = "published" if self._reply_visible(page, text) else "uncertain"
        self._like_open_post(page)
        return outcome

    def _already_replied(self, page: Page) -> bool:
        """True when this account already has a reply under the open post."""
        handle = (self.x_handle(page) or getattr(self.settings, "x_handle", "") or "").lower().lstrip("@")
        if not handle:
            return False
        try:
            found = page.evaluate(
                """handle => {
                  const articles = [...document.querySelectorAll('article[data-testid="tweet"]')].slice(1);
                  return articles.some(article => [...article.querySelectorAll('a[href]')].some(link => {
                    const href = (link.getAttribute('href') || '').toLowerCase();
                    return href === '/' + handle || href.startsWith('/' + handle + '/');
                  }));
                }""",
                handle,
            )
        except PlaywrightTimeout:
            return False
        return bool(found)

    def _click_covered(self, button) -> None:
        """Click even when X draws a mask over the button."""
        try:
            button.evaluate("el => el.click()")
        except Exception as exc:
            raise RuntimeError("X did not accept the click. Nothing was sent.") from exc

    def _like_open_post(self, page: Page) -> None:
        article = page.locator('article[data-testid="tweet"]').first
        already = article.locator('[data-testid="unlike"]')
        try:
            if already.count() and already.first.is_visible():
                return
            like = article.locator('[data-testid="like"]')
            if like.count() and like.first.is_visible():
                self._click_covered(like.first)
        except Exception:
            return

    def publish_post(self, text: str) -> str:
        """Post one original status. Raises before the click when nothing was sent."""
        page = self.open_x()
        if not self.x_logged_in(page):
            state = self.ensure_x()
            if state.status != "signed_in":
                raise RuntimeError(state.detail)
            page = self.open_x()
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1200)
        box = page.locator('[data-testid="tweetTextarea_0"]').first
        try:
            box.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout as exc:
            raise RuntimeError("X did not show the composer. Nothing was posted.") from exc
        box.fill(text)
        button = page.locator('[data-testid="tweetButton"]').first
        button.wait_for(state="visible", timeout=10_000)
        if button.is_disabled():
            raise RuntimeError("X left the post button disabled. Nothing was posted.")
        button.click()
        try:
            box.wait_for(state="hidden", timeout=15_000)
        except PlaywrightTimeout:
            return ""
        if "/status/" in page.url:
            return page.url
        return ""

    def confirm_reply(self, status_url: str, text: str) -> str:
        page = self.open_x()
        page.goto(status_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1000)
        return "published" if self._reply_visible(page, text) else "absent"

    def ask_chatgpt(
        self,
        prompt: str,
        cancelled: Callable[[], bool] | None = None,
        chat_url: str = "",
        settle: int = 8,
    ) -> str:
        """One ChatGPT chat. A call with a chat URL stays in that chat and does not open another."""
        pinned = _chatgpt_conversation(chat_url)
        if pinned:
            page = self._page("chatgpt.com")
            if _chatgpt_conversation(page.url or "") != pinned:
                page.goto(chat_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(1200)
            if self._chatgpt_composer(page) is None:
                self._wait_until(lambda: self._chatgpt_composer(page) is not None, cancelled, seconds=25)
        else:
            page = self.open_chatgpt()
        if self._chatgpt_composer(page) is None and not pinned:
            state = self.ensure_chatgpt(cancelled)
            if state.status != "signed_in":
                raise RuntimeError(state.detail)
            page = self._page("chatgpt.com")
        box = self._chatgpt_composer(page)
        if box is None:
            if pinned:
                raise RuntimeError("ChatGPT did not show the message box in the product chat.")
            raise RuntimeError("ChatGPT did not show the message box.")
        self._wait_for_chatgpt_reply(page, cancelled)
        before = self._assistant_replies(page)
        self._write_chatgpt_prompt(page, box, prompt)
        self._click_chatgpt_send(page, box)
        answer = self._read_answer(page, before, cancelled, prompt, settle)
        self._remember_chat(page)
        return answer

    def ask_grok(
        self,
        prompt: str,
        cancelled: Callable[[], bool] | None = None,
        chat_url: str = "",
        expect_posts: bool = False,
    ) -> str:
        """One Grok chat inside X. Later messages stay in that conversation."""
        page = self._page("x.com")
        target = chat_url or self.grok_url
        wanted = _grok_conversation(target)
        current = _grok_conversation(page.url or "")
        if wanted and wanted != current:
            page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(1200)
        elif "/grok" not in (page.url or ""):
            page = self.open_grok()
        if self._grok_limited(page):
            return LIMIT_NOTICE
        box = self._wait_grok_composer(page, cancelled)
        if self._grok_limited(page):
            return LIMIT_NOTICE
        if box is None:
            snippet = self._page_snippet(page)
            detail = f" X showed: {snippet}" if snippet else ""
            raise RuntimeError(f"Grok did not show the message box inside X.{detail}")
        if self._grok_busy(page):
            self._wait_until(lambda: not self._grok_busy(page), cancelled, seconds=120)
        if self._grok_limited(page):
            return LIMIT_NOTICE
        self._write_chatgpt_prompt(page, box, prompt)
        self._send_grok(page, box)
        answer = self._read_grok(page, prompt, cancelled, expect_posts)
        self._remember_grok(page)
        return answer

    def _remember_grok(self, page: Page) -> None:
        """Keep the conversation id. X puts it in the query, and dropping it starts a new chat."""
        deadline = time.time() + 12
        while time.time() < deadline:
            url = (page.url or "").split("#")[0]
            if _grok_conversation(url):
                self.grok_url = url
                return
            page.wait_for_timeout(400)
        url = (page.url or "").split("#")[0]
        if "/grok" in url:
            self.grok_url = url

    def _wait_grok_composer(self, page: Page, cancelled: Callable[[], bool] | None):
        deadline = time.time() + 25
        while time.time() < deadline:
            if cancelled and cancelled():
                raise RuntimeError("Stopped while opening Grok.")
            self._dismiss_cookies(page)
            self._dismiss_grok_gate(page)
            box = self._grok_composer(page)
            if box is not None:
                return box
            try:
                page.wait_for_timeout(500)
            except PlaywrightTimeout:
                return None
        return None

    def _dismiss_grok_gate(self, page: Page) -> None:
        for name in ("Agree", "I agree", "Continue", "Got it", "Try Grok", "Start"):
            button = page.get_by_role("button", name=name, exact=True)
            try:
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=1_500)
                    page.wait_for_timeout(400)
                    return
            except PlaywrightTimeout:
                return

    def _grok_composer(self, page: Page):
        surfaces = [page, *[frame for frame in page.frames if frame != page.main_frame]]
        selectors = (
            "textarea[data-testid='grokInput']",
            "textarea[data-testid='grok-input']",
            "[data-testid='grokInput'] textarea",
            "textarea[aria-label*='Ask Grok' i]",
            "textarea[aria-label*='Ask' i]",
            "textarea[placeholder*='Ask' i]",
            "div[contenteditable='true'][aria-label*='Ask' i]",
            "textarea",
            "div.ProseMirror[role='textbox']",
            "[role='textbox']",
            "div[contenteditable='true']",
        )
        for surface in surfaces:
            for selector in selectors:
                boxes = surface.locator(selector)
                try:
                    count = min(boxes.count(), 8)
                except Exception:
                    continue
                for index in range(count):
                    box = boxes.nth(index)
                    try:
                        if not box.is_visible():
                            continue
                        testid = box.get_attribute("data-testid") or ""
                        if "SearchBox" in testid:
                            continue
                        return box
                    except PlaywrightTimeout:
                        continue
        return None

    def _send_grok(self, page: Page, box) -> None:
        send = page.locator(
            'button[aria-label="Send"], button[aria-label="Submit"], button[data-testid="send-button"]'
        ).last
        try:
            if send.count() and send.is_visible() and not send.is_disabled():
                send.click()
                return
        except PlaywrightTimeout:
            pass
        box.press("Enter")

    def _grok_busy(self, page: Page) -> bool:
        stop = page.locator('button[aria-label*="Stop"], button:has-text("Stop")').first
        try:
            return stop.count() > 0 and stop.is_visible()
        except PlaywrightTimeout:
            return False

    def _page_text(self, page: Page) -> str:
        try:
            return str(page.evaluate("() => document.body ? document.body.innerText : ''") or "")
        except PlaywrightTimeout:
            return ""

    def _grok_surface_text(self, page: Page) -> str:
        """The Grok column plus the limit banner, including text inside an open shadow root."""
        script = """() => {
          const parts = [];
          const visit = (node) => {
            if (!node) return;
            if (node.shadowRoot) visit(node.shadowRoot);
            if (node.nodeType === Node.TEXT_NODE) {
              const text = node.textContent || '';
              if (text.trim()) parts.push(text);
              return;
            }
            for (const child of node.childNodes || []) visit(child);
          };
          visit(document.body);
          return parts.join('\\n');
        }"""
        chunks: list[str] = []
        surfaces = [page, *[frame for frame in page.frames if frame != page.main_frame]]
        for surface in surfaces:
            try:
                chunks.append(str(surface.evaluate(script) or ""))
            except Exception:
                continue
        return "\n".join(chunk for chunk in chunks if chunk)

    def _grok_limited(self, page: Page) -> bool:
        if limit_reached(self._grok_surface_text(page)):
            return True
        surfaces = [page, *[frame for frame in page.frames if frame != page.main_frame]]
        for surface in surfaces:
            for label in ("limit reached", "daily limit", "free tier limit"):
                try:
                    banner = surface.get_by_text(label, exact=False)
                    if banner.count() and banner.first.is_visible():
                        return True
                except Exception:
                    continue
        return False

    def _read_grok(
        self,
        page: Page,
        prompt: str,
        cancelled: Callable[[], bool] | None,
        expect_posts: bool,
    ) -> str:
        """Wait until the reply after this prompt stops growing."""
        needle = prompt.strip().split("\n", 1)[0][:40]
        last = ""
        stable = 0
        deadline = time.time() + 180
        while time.time() < deadline:
            if cancelled and cancelled():
                raise RuntimeError("Stopped.")
            current = self._grok_surface_text(page)
            fresh = current.rsplit(needle, 1)[-1].strip() if needle and needle in current else ""
            noticed = limit_reply(fresh, current)
            if limit_reached(noticed):
                return noticed
            has_posts = "/status/" in fresh
            settled = len(fresh) >= 40 and fresh == last and not self._grok_busy(page)
            if expect_posts and not has_posts:
                settled = False
            if settled:
                stable += 1
                if stable >= 4:
                    return fresh
            else:
                stable = 0
                last = fresh
            page.wait_for_timeout(1000)
        if last and (not expect_posts or "/status/" in last or limit_reached(last)):
            return last
        if self._grok_limited(page):
            return LIMIT_NOTICE
        raise RuntimeError("Grok did not finish a reply.")

    def _remember_chat(self, page: Page) -> None:
        """Keep the chat URL only after ChatGPT replaces the temporary local id."""
        deadline = time.time() + 10
        while time.time() < deadline:
            url = (page.url or "").split("?")[0]
            if "/c/" in url and "local-chatgpt" not in url:
                self.chatgpt_url = url
                return
            page.wait_for_timeout(400)

    def _assistant_replies(self, page: Page) -> list[str]:
        try:
            found = page.evaluate(
                """() => {
                  const nodes = [...document.querySelectorAll('[class*="MarkdownRoot"], [data-message-author-role="assistant"]')];
                  const texts = [];
                  const seen = new Set();
                  for (const node of nodes) {
                    const cls = String(node.className || '');
                    if (cls.includes('rich-text-user-turn')) continue;
                    if (node.closest('.rich-text-user-turn, [data-message-author-role="user"]')) continue;
                    const text = (node.innerText || '').trim();
                    if (!text || seen.has(text)) continue;
                    seen.add(text);
                    texts.push(text);
                  }
                  if (texts.length) return texts;
                  const body = document.body.innerText || '';
                  return body.split('ChatGPT said:').slice(1).map((part) => part.split('ChatGPT can make mistakes')[0].trim()).filter(Boolean);
                }"""
            )
        except PlaywrightTimeout:
            return []
        return [str(item).strip() for item in found or [] if str(item).strip()]

    def _read_answer(
        self,
        page: Page,
        before: list[str],
        cancelled: Callable[[], bool] | None,
        prompt: str = "",
        settle: int = 8,
    ) -> str:
        known = {" ".join(item.split()) for item in before}
        known.add(" ".join(prompt.split()))
        deadline = time.time() + 240
        stable = ""
        stable_hits = 0
        while time.time() < deadline:
            if cancelled and cancelled():
                raise RuntimeError("Stopped while waiting for ChatGPT.")
            replies = [item for item in self._assistant_replies(page) if " ".join(item.split()) not in known]
            substantial = [
                item
                for item in replies
                if len(item) >= 80 and not _TOOL_LINE.match(item.strip()) and not _STATUS_LINE.match(item.strip()) and not is_preamble(item)
            ]
            current = substantial[-1] if substantial else ""
            latest = replies[-1] if replies else ""
            if _TOOL_LINE.match(latest.strip()) or _STATUS_LINE.match(latest.strip()):
                latest = ""
            tracked = current or latest
            # A search chip after the writeup means ChatGPT is still working.
            busy = self._chatgpt_busy(page) or bool(replies and current and replies[-1] != current)
            if tracked and tracked == stable and not busy:
                stable_hits += 1
            else:
                stable = tracked
                stable_hits = 0
            if tracked and stable_hits >= max(1, settle) and not busy:
                return tracked
            page.wait_for_timeout(1000)
        if self._chatgpt_busy(page):
            raise RuntimeError("ChatGPT has not finished the reply. The next message was not sent.")
        if stable and " ".join(stable.split()) not in known:
            return stable
        raise RuntimeError("ChatGPT did not finish the product details.")

    def _wait_for_chatgpt_reply(self, page: Page, cancelled: Callable[[], bool] | None) -> None:
        """Do not type the next message while the previous reply is still being written."""
        if not self._chatgpt_busy(page):
            return
        finished = self._wait_until(lambda: not self._chatgpt_busy(page), cancelled, seconds=240)
        if cancelled and cancelled():
            raise RuntimeError("Stopped while waiting for ChatGPT.")
        if not finished or self._chatgpt_busy(page):
            raise RuntimeError("ChatGPT is still writing the previous reply. The next message was not sent.")

    def _chatgpt_busy(self, page: Page) -> bool:
        """True while ChatGPT is still producing the current reply."""
        try:
            if page.evaluate(
                """() => {
                  const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 2 && rect.height > 2;
                  };
                  for (const button of document.querySelectorAll('button')) {
                    const label = (button.getAttribute('aria-label') || '') + ' ' + (button.getAttribute('data-testid') || '');
                    if (visible(button) && /stop/i.test(label)) return true;
                  }
                  if (document.querySelector('.result-streaming')) return true;
                  const main = document.querySelector('main') || document.body;
                  for (const node of main.querySelectorAll('span, div')) {
                    if (node.childElementCount) continue;
                    const text = (node.textContent || '').trim();
                    if ((text === 'Thinking' || text === 'Searching') && visible(node)) return true;
                  }
                  return false;
                }""",
                timeout=3_000,
            ):
                return True
        except Exception:
            pass
        stop = page.locator(
            'button[data-testid="stop-button"], button[aria-label="Stop generating"], '
            'button[aria-label="Stop streaming"], button[aria-label="Stop response"], button[aria-label="Stop"]'
        )
        try:
            return stop.count() > 0 and stop.first.is_visible()
        except PlaywrightTimeout:
            return False

    def _reply_visible(self, page: Page, text: str) -> bool:
        snippet = " ".join(text.split())[:40]
        if len(snippet) < 8:
            return False
        try:
            return page.get_by_text(snippet, exact=False).count() > 0
        except PlaywrightTimeout:
            return False

    def _replies_limited(self, page: Page) -> bool:
        try:
            text = page.locator('[data-testid="primaryColumn"]').inner_text(timeout=3_000)
        except PlaywrightTimeout:
            return False
        lowered = text.lower()
        return "who can reply" in lowered or "replies are limited" in lowered

    def _challenge_visible(self, page: Page) -> bool:
        code = page.locator('input[name="code"], input[autocomplete="one-time-code"]')
        try:
            return code.count() > 0 and code.first.is_visible()
        except PlaywrightTimeout:
            return False

    def _wait_until(
        self,
        check: Callable[[], bool],
        cancelled: Callable[[], bool] | None,
        seconds: int = 180,
        tick: Callable[[], None] | None = None,
    ) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cancelled and cancelled():
                return False
            if check():
                return True
            if tick:
                tick()
            time.sleep(1)
        return False

    def _dismiss_cookies(self, page: Page) -> None:
        for name in ("Accept all cookies", "Accept all"):
            button = page.get_by_role("button", name=name)
            try:
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=1500)
                    return
            except PlaywrightTimeout:
                return
