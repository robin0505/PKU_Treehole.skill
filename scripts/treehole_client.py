#!/usr/bin/env python3
"""PKU Treehole reader backed by a logged-in Chrome debug page."""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

import requests

def _env_int(name: str, default: int) -> int:
    raw_value = str(os.getenv(name, "")).strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


DEBUG_HOST = str(os.getenv("TREEHOLE_DEBUG_HOST", "localhost")).strip() or "localhost"
DEBUG_PORT = _env_int("TREEHOLE_DEBUG_PORT", 9222)
DEBUG_ENDPOINT = str(
    os.getenv("TREEHOLE_DEBUG_ENDPOINT", "http://{0}:{1}/json/version".format(DEBUG_HOST, DEBUG_PORT))
).strip()
TREEHOLE_URL = str(os.getenv("TREEHOLE_URL", "https://treehole.pku.edu.cn/web/")).strip()
RATE_LIMIT_PER_MIN = 18
MIN_INTERVAL_S = 60.0 / RATE_LIMIT_PER_MIN
JITTER_S = 0.8
INITIAL_LOAD_WAIT_MS = 250
ACTION_SETTLE_MS = 250
SCROLL_SETTLE_MS = 250
SCROLL_DELTA_Y = 6000
POST_SEARCH_WAIT_TIMEOUT_S = 4.0
POST_SEARCH_POLL_TIMEOUT_S = 6.0
COMMENT_WAIT_TIMEOUT_S = 12.0
COMMENT_STALL_ROUNDS = 4
KNOWN_TAGS = {
    1: "课程心得",
    2: "失物招领",
    3: "求职经历",
    5: "跳蚤市场",
}

_last_action_at = 0.0

# ── Proxy env-var names (both cases) that should be neutralised ──
_PROXY_ENV_VARS = [
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
    "FTP_PROXY",
    "ftp_proxy",
    "SOCKS_PROXY",
    "socks_proxy",
]

_proxies_disabled = False


def _disable_system_proxies() -> None:
    """Remove all proxy-related environment variables so that neither
    ``requests``, Playwright, nor any spawned sub-process picks up
    system / VPN proxy settings (Clash, V2Ray, etc.).

    This is idempotent — subsequent calls are no-ops.
    """
    global _proxies_disabled
    if _proxies_disabled:
        return

    for var in _PROXY_ENV_VARS:
        os.environ.pop(var, None)

    # Also patch the default requests Session so that even if a library
    # re-reads os.environ we still bypass proxies.
    try:
        _original_env_get = requests.Session().merge_environment_settings

        def _no_proxy_merge(self, url, proxies, stream, verify, cert):  # type: ignore[override]
            return _original_env_get(url, {}, stream, verify, cert)

        requests.Session.merge_environment_settings = _no_proxy_merge  # type: ignore[assignment]
    except Exception:
        pass

    _proxies_disabled = True


def _env_flag(name: str) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _throttle() -> float:
    global _last_action_at

    elapsed = time.time() - _last_action_at
    wait_for = (MIN_INTERVAL_S + random.uniform(0.0, JITTER_S)) - elapsed
    waited = 0.0
    if wait_for > 0:
        time.sleep(wait_for)
        waited = wait_for
    _last_action_at = time.time()
    return waited


def _read_debug_ws_url() -> str:
    try:
        response = requests.get(DEBUG_ENDPOINT, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not reach Chrome debug endpoint {0}. Open Chrome with "
            "--remote-debugging-port={1} and keep Treehole logged in."
            .format(DEBUG_ENDPOINT, DEBUG_PORT)
        ) from exc

    try:
        return response.json()["webSocketDebuggerUrl"]
    except (KeyError, ValueError) as exc:
        raise RuntimeError("Chrome debug endpoint did not return a websocket URL.") from exc


def _normalize_pid(pid: Any) -> int:
    return int(str(pid).lstrip("#"))


def _normalize_comment_text(comment: Dict[str, Any]) -> str:
    text = str(comment.get("text", ""))
    name = str(comment.get("name", "") or "")
    prefixes = []
    if name:
        prefixes.append("[{0}] ".format(name))
    if comment.get("islz"):
        prefixes.append("[洞主] ")

    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _safe_disconnect(browser: Any) -> None:
    disconnect = getattr(browser, "disconnect", None)
    if callable(disconnect):
        disconnect()


class TreeholeClient:
    """Read-only client that drives the logged-in Treehole web app itself."""

    def __init__(self) -> None:
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._detail_cache: Dict[str, Any] = {}
        self.debug_timing = _env_flag("TREEHOLE_DEBUG_TIMING")

    def set_debug_timing(self, enabled: bool) -> None:
        self.debug_timing = bool(enabled)

    def _debug(self, message: str) -> None:
        if not self.debug_timing:
            return
        print("[treehole-timing] {0}".format(message), file=sys.stderr, flush=True)

    def _measure(self, label: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._debug("{0}: {1:.1f} ms".format(label, elapsed_ms))

    def __enter__(self) -> "TreeholeClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        if self.page is not None:
            try:
                if not self.page.is_closed():
                    self.page.close()
            except Exception:
                pass
            self.page = None

        if self.browser is not None:
            try:
                _safe_disconnect(self.browser)
            except Exception:
                pass
            self.browser = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        self.context = None
        self._detail_cache = {}

    def _ensure_session(self) -> None:
        if self.context is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required. Install it in your active Python environment "
                "(for example: python3 -m pip install playwright)."
            ) from exc

        ws_url = _read_debug_ws_url()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(ws_url)
        if not self.browser.contexts:
            raise RuntimeError(
                "No Chrome context is available on the debug port. Open Treehole in that Chrome first."
            )
        self.context = self.browser.contexts[0]

    def _open_fresh_page(self) -> None:
        started = time.perf_counter()
        self._ensure_session()

        if self.page is not None:
            try:
                if not self.page.is_closed():
                    self.page.close()
            except Exception:
                pass

        self.page = self.context.new_page()
        try:
            self.page.set_viewport_size({"width": 1280, "height": 900})
        except Exception:
            pass

        waited = _throttle()
        self._debug("throttle before page.goto: {0:.3f} s".format(waited))
        self.page.goto(TREEHOLE_URL, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(INITIAL_LOAD_WAIT_MS)
        self._assert_logged_in()
        self._detail_cache = {}
        self._debug("open fresh page total: {0:.1f} ms".format((time.perf_counter() - started) * 1000.0))

    def _assert_logged_in(self) -> None:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        state = self.page.evaluate(
            """
            () => {
                const app = document.querySelector('#app');
                const root = app && app.__vue__;
                const indexVm = root && root.$children && root.$children[0];
                return {
                    hasRoot: !!root,
                    hasIndex: !!indexVm,
                    text: document.body.innerText.slice(0, 200),
                };
            }
            """
        )

        if not state.get("hasRoot") or not state.get("hasIndex"):
            raise RuntimeError("Treehole page did not finish loading in the debug Chrome instance.")
        if "北大树洞" not in str(state.get("text", "")):
            raise RuntimeError(
                "The debug Chrome instance is not on a logged-in Treehole page. Open {0} first."
                .format(TREEHOLE_URL)
            )

    def _wait_for_posts(
        self,
        min_count: int = 1,
        timeout_s: float = 15.0,
        require_loading_idle: bool = True,
    ) -> None:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        deadline = time.time() + timeout_s
        stable_rounds = 0
        last_len = -1
        loops = 0
        started = time.perf_counter()

        while time.time() < deadline:
            loops += 1
            state = self.page.evaluate(
                """
                () => {
                    const indexVm = document.querySelector('#app').__vue__.$children[0];
                    return {
                        loading: !!indexVm.loading,
                        length: Array.isArray(indexVm.list) ? indexVm.list.length : 0,
                    };
                }
                """
            )
            loading = bool(state.get("loading"))
            current_len = int(state.get("length", 0))
            loading_ready = (not require_loading_idle) or (not loading)
            if loading_ready and current_len >= min_count:
                if current_len == last_len:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 2:
                    self._debug(
                        "wait for posts done: loops={0}, len={1}, loading={2}, elapsed={3:.1f} ms".format(
                            loops,
                            current_len,
                            loading,
                            (time.perf_counter() - started) * 1000.0,
                        )
                    )
                    return
            else:
                stable_rounds = 0
            last_len = current_len
            self.page.wait_for_timeout(600)

        self._debug(
            "wait for posts timeout: loops={0}, last_len={1}, require_idle={2}, elapsed={3:.1f} ms".format(
                loops,
                last_len,
                require_loading_idle,
                (time.perf_counter() - started) * 1000.0,
            )
        )

    def _target_post_state(self, pid: int) -> Dict[str, Any]:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        return dict(
            self.page.evaluate(
                """
                () => {
                    const indexVm = document.querySelector('#app').__vue__.$children[0];
                    const list = Array.isArray(indexVm.list) ? indexVm.list : [];
                    const hasPid = list.some(item => Number(item.pid || 0) === Number(%PID%));
                    return {
                        loading: !!indexVm.loading,
                        length: list.length,
                        hasPid,
                    };
                }
                """.replace("%PID%", str(pid))
            )
        )

    def _wait_for_target_post(self, pid: int, timeout_s: float = POST_SEARCH_POLL_TIMEOUT_S) -> bool:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        deadline = time.time() + timeout_s
        stable_rounds = 0
        last_len = -1
        loops = 0
        started = time.perf_counter()

        while time.time() < deadline:
            loops += 1
            state = self._target_post_state(pid)
            current_len = int(state.get("length", 0))
            has_pid = bool(state.get("hasPid"))
            if has_pid:
                if current_len == last_len:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 1:
                    self._debug(
                        "wait for target pid done: loops={0}, len={1}, elapsed={2:.1f} ms".format(
                            loops,
                            current_len,
                            (time.perf_counter() - started) * 1000.0,
                        )
                    )
                    return True
            else:
                stable_rounds = 0
            last_len = current_len
            self.page.wait_for_timeout(300)

        self._debug(
            "wait for target pid timeout: loops={0}, last_len={1}, elapsed={2:.1f} ms".format(
                loops,
                last_len,
                (time.perf_counter() - started) * 1000.0,
            )
        )
        return False

    def _extract_posts(self) -> List[Dict[str, Any]]:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        return list(
            self.page.evaluate(
                """
                () => {
                    const indexVm = document.querySelector('#app').__vue__.$children[0];
                    return (indexVm.list || []).map(item => ({
                        pid: item.pid,
                        text: item.text || '',
                        type: item.type || '',
                        timestamp: item.timestamp || null,
                        reply: item.reply || 0,
                        likenum: item.likenum || 0,
                        tag: item.tag || '',
                        url: item.url || null,
                        label: item.label || '',
                        is_follow: item.is_follow || 0,
                    }));
                }
                """
            )
        )

    def _extract_comments(self, pid: int) -> List[Dict[str, Any]]:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        data = self.page.evaluate(
            """
            () => {
                const indexVm = document.querySelector('#app').__vue__.$children[0];
                const replyVms = indexVm.$children.filter(component => {
                    const name = component.$options.name || component.$options._componentTag;
                    return name === 'reply';
                });
                return replyVms.map(component => ({
                    pid: Number(component.pid || 0),
                    total: Number(component.total || component.reply || 0),
                    loading: !!component.loading,
                    data: Array.isArray(component.data) ? component.data.map(item => ({
                        cid: item.cid,
                        pid: item.pid,
                        text: item.text || '',
                        name: item.name || '',
                        islz: item.islz,
                        quote_text: item.quote_text || null,
                        timestamp: item.timestamp || null,
                    })) : [],
                }));
            }
            """
        )

        for item in data:
            if int(item.get("pid", 0)) == pid:
                comments = list(item.get("data", []))
                for comment in comments:
                    comment["text"] = _normalize_comment_text(comment)
                return comments
        return []

    def _fetch_all_comments_via_page(self, pid: int) -> List[Dict[str, Any]]:
        """Fetch all comments by calling the API via fetch() inside the browser page.

        This keeps requests indistinguishable from normal webapp behavior since
        they originate from the same tab with identical cookies, headers, and
        browser fingerprint.
        """
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        started = time.perf_counter()

        # First page: discover pagination metadata. The web app's axios
        # interceptor reads the bearer token from the pku_token cookie and
        # sends Uuid from localStorage. localStorage.token is not used here.
        first_result = self.page.evaluate(
            """
            async () => {
                function getCookie(name) {
                    const escaped = name.replace(/[.$?*|{}()\\[\\]\\/\\+^]/g, '\\\\$&');
                    const match = document.cookie.match(new RegExp('(?:^|; )' + escaped + '=([^;]*)'));
                    return match ? decodeURIComponent(match[1]) : '';
                }
                const token = getCookie('pku_token');
                const uuid = localStorage.getItem('pku-uuid') || '';
                const resp = await fetch(
                    '/api/pku_comment_v3/%PID%?page=1&limit=10',
                    {
                        headers: {
                            'Authorization': 'Bearer ' + token,
                            'Uuid': uuid,
                            'Accept': 'application/json, text/plain, */*',
                        },
                        credentials: 'same-origin',
                    }
                );
                return await resp.json();
            }
            """.replace("%PID%", str(pid))
        )

        if not first_result or not first_result.get("success"):
            self._debug("fetch comments API failed for page 1, falling back")
            return []

        page_data = first_result.get("data", {})
        all_comments = list(page_data.get("data", []))
        total = int(page_data.get("total", 0))
        last_page = int(page_data.get("last_page", 1))
        self._debug(
            "fetch comments page 1: got={0}, total={1}, last_page={2}".format(
                len(all_comments), total, last_page
            )
        )

        # Fetch remaining pages
        for page_num in range(2, last_page + 1):
            # Small delay between pages to mimic human behavior
            self.page.wait_for_timeout(int(200 + random.uniform(0, 300)))
            page_result = self.page.evaluate(
                """
                async () => {
                    function getCookie(name) {
                        const escaped = name.replace(/[.$?*|{}()\\[\\]\\/\\+^]/g, '\\\\$&');
                        const match = document.cookie.match(new RegExp('(?:^|; )' + escaped + '=([^;]*)'));
                        return match ? decodeURIComponent(match[1]) : '';
                    }
                    const token = getCookie('pku_token');
                    const uuid = localStorage.getItem('pku-uuid') || '';
                    const resp = await fetch(
                        '/api/pku_comment_v3/%PID%?page=%PAGE%&limit=10',
                        {
                            headers: {
                                'Authorization': 'Bearer ' + token,
                                'Uuid': uuid,
                                'Accept': 'application/json, text/plain, */*',
                            },
                            credentials: 'same-origin',
                        }
                    );
                    return await resp.json();
                }
                """.replace("%PID%", str(pid)).replace("%PAGE%", str(page_num))
            )
            if page_result and page_result.get("success"):
                page_comments = page_result.get("data", {}).get("data", [])
                all_comments.extend(page_comments)
                self._debug(
                    "fetch comments page {0}: got={1}".format(page_num, len(page_comments))
                )
            else:
                self._debug("fetch comments page {0}: API failed".format(page_num))
                break

        # Normalize comment fields to match the format used by _extract_comments
        normalized: List[Dict[str, Any]] = []
        for c in all_comments:
            comment = {
                "cid": c.get("cid"),
                "pid": c.get("pid"),
                "text": c.get("text", ""),
                "name": c.get("name", ""),
                "islz": c.get("islz") or (c.get("name") == "洞主"),
                "quote_text": None,
                "timestamp": c.get("timestamp"),
            }
            # Handle quote field (API uses "quote" object)
            quote = c.get("quote")
            if isinstance(quote, dict):
                comment["quote_text"] = quote.get("text", "")
            comment["text"] = _normalize_comment_text(comment)
            normalized.append(comment)

        elapsed = (time.perf_counter() - started) * 1000.0
        self._debug(
            "fetch all comments via page total: {0} comments in {1:.1f} ms".format(
                len(normalized), elapsed
            )
        )
        return normalized

    def _reply_state(self, pid: int) -> Dict[str, Any]:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        return dict(
            self.page.evaluate(
                """
                () => {
                    const indexVm = document.querySelector('#app').__vue__.$children[0];
                    const replyVm = indexVm.$children.find(component => {
                        const name = component.$options.name || component.$options._componentTag;
                        return name === 'reply' && Number(component.pid || 0) === Number(%PID%);
                    });
                    if (!replyVm) {
                        return {exists: false, loading: false, total: 0, dataLen: 0};
                    }
                    return {
                        exists: true,
                        loading: !!replyVm.loading,
                        total: Number(replyVm.total || replyVm.reply || 0),
                        dataLen: Array.isArray(replyVm.data) ? replyVm.data.length : 0,
                    };
                }
                """.replace("%PID%", str(pid))
            )
        )

    def _trigger_comment_refresh(self, pid: int) -> bool:
        if self.page is None:
            return False

        return bool(
            self.page.evaluate(
                """
                () => {
                    const indexVm = document.querySelector('#app').__vue__.$children[0];
                    const replyVm = indexVm.$children.find(component => {
                        const name = component.$options.name || component.$options._componentTag;
                        return name === 'reply' && Number(component.pid || 0) === Number(%PID%);
                    });
                    if (!replyVm || typeof replyVm.getData !== 'function') {
                        return false;
                    }
                    replyVm.getData();
                    return true;
                }
                """.replace("%PID%", str(pid))
            )
        )

    def _wait_for_comments(self, pid: int, expected: int, timeout_s: float = COMMENT_WAIT_TIMEOUT_S) -> None:
        if self.page is None or expected <= 0:
            return

        deadline = time.time() + timeout_s
        stable_rounds = 0
        stall_rounds = 0
        refresh_attempted = False
        last_len = -1
        loops = 0
        started = time.perf_counter()

        while time.time() < deadline:
            loops += 1
            state = self._reply_state(pid)
            loading = bool(state.get("loading"))
            data_len = int(state.get("dataLen", 0))
            total = int(state.get("total", expected)) or expected
            if data_len > last_len:
                stall_rounds = 0
            else:
                stall_rounds += 1

            if state.get("exists") and not loading and data_len >= min(expected, total):
                if data_len == last_len:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 2:
                    self._debug(
                        "wait for comments done: loops={0}, got={1}/{2}, elapsed={3:.1f} ms".format(
                            loops,
                            data_len,
                            total,
                            (time.perf_counter() - started) * 1000.0,
                        )
                    )
                    return
            else:
                stable_rounds = 0

            # Some threads stop at a partial page (commonly 10 rows) in the page state.
            # Avoid waiting full timeout when no progress is happening.
            if state.get("exists") and not loading and data_len < min(expected, total):
                if (not refresh_attempted) and stall_rounds >= 2:
                    refresh_attempted = True
                    triggered = self._trigger_comment_refresh(pid)
                    self._debug("comment refresh attempted: {0}".format(triggered))
                elif stall_rounds >= COMMENT_STALL_ROUNDS:
                    self._debug(
                        "wait for comments early-stop: loops={0}, got={1}/{2}, stall_rounds={3}, elapsed={4:.1f} ms".format(
                            loops,
                            data_len,
                            total,
                            stall_rounds,
                            (time.perf_counter() - started) * 1000.0,
                        )
                    )
                    return

            last_len = data_len
            self.page.wait_for_timeout(500)

        self._debug(
            "wait for comments timeout: loops={0}, last={1}/{2}, elapsed={3:.1f} ms".format(
                loops,
                last_len,
                expected,
                (time.perf_counter() - started) * 1000.0,
            )
        )

    def _run_search(
        self,
        keyword: str,
        wait_timeout_s: float = 15.0,
        require_loading_idle: bool = True,
    ) -> None:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        keyword_json = json.dumps(keyword, ensure_ascii=False)
        started = time.perf_counter()
        waited = _throttle()
        self._debug("throttle before search: {0:.3f} s".format(waited))
        self.page.evaluate(
            """
            () => {
                const indexVm = document.querySelector('#app').__vue__.$children[0];
                const headerVm = indexVm.$children.find(component => {
                    const name = component.$options.name || component.$options._componentTag;
                    return name === 'headerTop';
                });
                headerVm.keyword = %KEYWORD%;
                headerVm.search();
            }
            """.replace("%KEYWORD%", keyword_json)
        )
        self._wait_for_posts(min_count=1, timeout_s=wait_timeout_s, require_loading_idle=require_loading_idle)
        self.page.wait_for_timeout(ACTION_SETTLE_MS)
        self._debug("run search total ({0}): {1:.1f} ms".format(keyword, (time.perf_counter() - started) * 1000.0))

    def _scroll_for_more_posts(self) -> int:
        if self.page is None:
            raise RuntimeError("Treehole page is not open.")

        waited = _throttle()
        self._debug("throttle before scroll: {0:.3f} s".format(waited))
        self.page.mouse.wheel(0, SCROLL_DELTA_Y)
        self.page.wait_for_timeout(SCROLL_SETTLE_MS)
        return len(self._extract_posts())

    def _filter_posts(self, posts: List[Dict[str, Any]], tag_id: Optional[int]) -> List[Dict[str, Any]]:
        if tag_id is None:
            return posts
        tag_name = KNOWN_TAGS.get(tag_id)
        if not tag_name:
            return []
        return [post for post in posts if str(post.get("tag") or "") == tag_name]

    def _dedupe_posts(self, posts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        unique_posts: List[Dict[str, Any]] = []
        for post in posts:
            pid = post.get("pid")
            if pid in seen:
                continue
            seen.add(pid)
            unique_posts.append(post)
        return unique_posts

    def _load_posts(
        self,
        keyword: str,
        max_pages: int = 5,
        limit: int = 25,
        tag_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if max_pages <= 0 or limit <= 0:
            return []

        self._open_fresh_page()
        if keyword:
            self._run_search(keyword)
        else:
            self._wait_for_posts(min_count=1)

        target_items = max_pages * limit
        posts = self._extract_posts()
        stalled_rounds = 0

        while len(posts) < target_items and stalled_rounds < 2:
            previous_count = len(posts)
            new_count = self._scroll_for_more_posts()
            posts = self._extract_posts()
            if new_count <= previous_count:
                stalled_rounds += 1
            else:
                stalled_rounds = 0

        posts = self._dedupe_posts(posts)
        posts = self._filter_posts(posts, tag_id)
        return posts[:target_items]

    def _load_post_bundle(self, pid: Any, use_api_comments: bool = False) -> Dict[str, Any]:
        pid_int = _normalize_pid(pid)
        cache_key = (pid_int, use_api_comments)
        if self._detail_cache.get("cache_key") == cache_key:
            return self._detail_cache

        started = time.perf_counter()
        self._measure("open fresh page", self._open_fresh_page)
        self._measure(
            "run post search",
            self._run_search,
            "#{0}".format(pid_int),
            POST_SEARCH_WAIT_TIMEOUT_S,
            False,
        )
        self._measure("wait for target pid", self._wait_for_target_post, pid_int, POST_SEARCH_POLL_TIMEOUT_S)
        all_posts = self._measure("extract posts", self._extract_posts)
        posts = [post for post in all_posts if int(post.get("pid", 0)) == pid_int]
        post = posts[0] if posts else {}
        expected_replies = int(post.get("reply", 0) or 0)
        self._debug("post found: {0}, expected replies: {1}".format(bool(post), expected_replies))

        comments: List[Dict[str, Any]] = []
        if use_api_comments and expected_replies > 0:
            # Use in-page fetch() to call the API directly — same browser
            # fingerprint, cookies, and headers as the webapp itself.
            comments = self._measure(
                "fetch all comments via page",
                self._fetch_all_comments_via_page,
                pid_int,
            )
            if not comments:
                self._debug("API comment fetch returned empty, falling back to UI extraction")
                self._measure("wait for comments", self._wait_for_comments, pid_int, expected_replies)
                comments = self._measure("extract comments", self._extract_comments, pid_int)
        else:
            self._measure("wait for comments", self._wait_for_comments, pid_int, expected_replies)
            comments = self._measure("extract comments", self._extract_comments, pid_int)

        self._detail_cache = {
            "cache_key": cache_key,
            "pid": pid_int,
            "post": post,
            "comments": comments,
        }
        self._debug("load post bundle total: {0:.1f} ms".format((time.perf_counter() - started) * 1000.0))
        return self._detail_cache

    def get_posts(self, page: int = 1, limit: int = 25) -> List[Dict[str, Any]]:
        page = max(page, 1)
        posts = self._load_posts(keyword="", max_pages=page, limit=limit)
        start = (page - 1) * limit
        end = start + limit
        return posts[start:end]

    def iter_posts(self, max_pages: int = 5, limit: int = 25) -> Iterator[Dict[str, Any]]:
        for post in self._load_posts(keyword="", max_pages=max_pages, limit=limit):
            yield post

    def search(
        self,
        keyword: str,
        page: int = 1,
        limit: int = 25,
        tag_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        page = max(page, 1)
        posts = self._load_posts(keyword=keyword, max_pages=page, limit=limit, tag_id=tag_id)
        start = (page - 1) * limit
        end = start + limit
        return posts[start:end]

    def search_all(
        self,
        keyword: str,
        max_pages: int = 10,
        limit: int = 25,
        tag_id: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        for item in self._load_posts(keyword=keyword, max_pages=max_pages, limit=limit, tag_id=tag_id):
            yield item

    def get_post(self, pid: Any) -> Dict[str, Any]:
        return dict(self._load_post_bundle(pid).get("post", {}))

    def get_comments(self, pid: Any, limit: int = 100) -> List[Dict[str, Any]]:
        comments = list(self._load_post_bundle(pid).get("comments", []))
        return comments[:limit]

    def get_all_comments(self, pid: Any) -> List[Dict[str, Any]]:
        """Fetch all comments for a post using the in-page API approach."""
        return list(self._load_post_bundle(pid, use_api_comments=True).get("comments", []))

    def get_comments_paged(
        self,
        pid: Any,
        per_page: int = 50,
    ) -> Iterator[Dict[str, Any]]:
        comments = list(self._load_post_bundle(pid, use_api_comments=True).get("comments", []))
        if per_page <= 0:
            per_page = len(comments) or 1
        for comment in comments:
            yield comment

    def get_tags(self) -> List[Dict[str, Any]]:
        return [{"id": tag_id, "name": tag_name} for tag_id, tag_name in KNOWN_TAGS.items()]

    def get_bookmarks(self) -> List[Dict[str, Any]]:
        raise RuntimeError("Bookmarks are not available in direct-page mode yet.")


def extract_cookies_from_chrome() -> Dict[str, str]:
    raise RuntimeError(
        "Cookie extraction has been removed. Keep Treehole logged in on Chrome debug port {0} and use build_client_from_chrome()."
        .format(DEBUG_PORT)
    )


def build_client_from_chrome() -> TreeholeClient:
    return TreeholeClient()


def materialize(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list(items)
