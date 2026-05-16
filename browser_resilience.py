"""
browser_resilience.py — Playwright Auto-Recovery Wrapper
=========================================================
Wraps Playwright browser lifecycle with automatic crash recovery.

Usage:
    async with BrowserRecoveryContext(emit_fn=sse_emit) as ctx:
        await ctx.safe_navigate('https://example.com')
        content = await ctx.page.content()

Features:
    - page.on('crash') → auto-reinitialize
    - browser.on('disconnected') → auto-reinitialize  
    - Up to MAX_RETRIES=2 recovery attempts
    - Emits nx:browser:recovering / nx:browser:recovered / nx:browser:failed
    - safe_navigate() with configurable timeout and error surfacing
    - Synchronous context via run_with_recovery() for non-async callers
"""

import asyncio
import logging
import uuid
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
        Error as PWError,
    )
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False
    PWError = Exception
    logger.warning("[BrowserResilience] playwright not installed — BrowserRecoveryContext is a no-op stub")


class BrowserRecoveryContext:
    """
    Playwright browser lifecycle manager with automatic crash recovery.

    Emits the following events via emit_fn(event_name, payload):
        nx:browser:recovering  — { reason, attempt, max }
        nx:browser:recovered   — { reason, attempt }
        nx:browser:failed      — { reason, retries, error_code }
        nx:browser:nav_failed  — { url, error }
    """

    MAX_RETRIES: int   = 2
    REINIT_DELAY: float = 2.0  # seconds before relaunch attempt

    def __init__(
        self,
        emit_fn: Optional[Callable] = None,
        headless: bool = True,
        launch_args: Optional[list] = None,
    ):
        self._emit = emit_fn or (lambda k, p: None)
        self._headless = headless
        self._launch_args = launch_args or [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-extensions',
        ]
        self._pw        = None
        self._browser: Optional[Browser]        = None
        self._context:  Optional[BrowserContext] = None
        self._page:     Optional[Page]           = None
        self._retry_count: int = 0
        self._recovering: bool = False
        self._context_id: str  = uuid.uuid4().hex[:8]

    # ── Async context manager ────────────────────────────────────────────────

    async def __aenter__(self) -> 'BrowserRecoveryContext':
        if not _PW_AVAILABLE:
            raise RuntimeError("playwright is not installed. Run: pip install playwright && playwright install chromium")
        await self._launch()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # ── Launch / close ───────────────────────────────────────────────────────

    async def _launch(self) -> None:
        """Launch a fresh browser, context, and page."""
        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=self._launch_args,
        )
        self._context = await self._browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent=(
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ),
        )
        self._page = await self._context.new_page()

        # Register crash/disconnect handlers
        self._page.on('crash',           self._on_page_crash)
        self._browser.on('disconnected', self._on_browser_disconnect)

        self._retry_count = 0
        self._recovering  = False
        logger.info('[Browser:%s] Launched successfully (headless=%s)', self._context_id, self._headless)

    async def close(self, silent: bool = False) -> None:
        """Gracefully close all Playwright objects, suppressing errors."""
        for obj, name in [
            (self._page,    'page'),
            (self._context, 'context'),
            (self._browser, 'browser'),
            (self._pw,      'playwright'),
        ]:
            if obj is not None:
                try:
                    await obj.close()
                except Exception as e:
                    if not silent:
                        logger.debug('[Browser:%s] Error closing %s: %s', self._context_id, name, e)
        self._page = self._context = self._browser = self._pw = None

    # ── Crash handlers ───────────────────────────────────────────────────────

    def _on_page_crash(self, _page: Any) -> None:
        logger.warning('[Browser:%s] Page crashed — scheduling recovery', self._context_id)
        asyncio.create_task(self._recover('page_crash'))

    def _on_browser_disconnect(self, _browser: Any) -> None:
        logger.warning('[Browser:%s] Browser disconnected — scheduling recovery', self._context_id)
        asyncio.create_task(self._recover('browser_disconnect'))

    # ── Recovery ─────────────────────────────────────────────────────────────

    async def _recover(self, reason: str) -> None:
        """Attempt to reinitialize the browser after a crash."""
        if self._recovering:
            logger.debug('[Browser:%s] Recovery already in progress — skipping', self._context_id)
            return

        if self._retry_count >= self.MAX_RETRIES:
            logger.error(
                '[Browser:%s] Max retries (%d) exhausted. Reason: %s',
                self._context_id, self.MAX_RETRIES, reason,
            )
            self._emit('nx:browser:failed', {
                'reason':     reason,
                'retries':    self._retry_count,
                'error_code': 'browser_max_retries',
                'context_id': self._context_id,
            })
            return

        self._recovering   = True
        self._retry_count += 1

        self._emit('nx:browser:recovering', {
            'reason':     reason,
            'attempt':    self._retry_count,
            'max':        self.MAX_RETRIES,
            'context_id': self._context_id,
        })

        logger.info(
            '[Browser:%s] Recovery attempt %d/%d for reason: %s',
            self._context_id, self._retry_count, self.MAX_RETRIES, reason,
        )

        await asyncio.sleep(self.REINIT_DELAY)

        try:
            await self.close(silent=True)
            await self._launch()
            self._recovering = False
            self._emit('nx:browser:recovered', {
                'reason':     reason,
                'attempt':    self._retry_count,
                'context_id': self._context_id,
            })
            logger.info('[Browser:%s] Recovered successfully (attempt %d)', self._context_id, self._retry_count)
        except Exception as exc:
            logger.error('[Browser:%s] Recovery launch failed: %s', self._context_id, exc)
            self._recovering = False
            # Recurse to attempt again (up to MAX_RETRIES)
            await self._recover(f'recovery_failed:{reason}')

    # ── Public helpers ───────────────────────────────────────────────────────

    @property
    def page(self) -> Optional[Page]:
        """The active Playwright page. None if browser has crashed and recovery failed."""
        return self._page

    @property
    def is_alive(self) -> bool:
        """True if the browser is currently connected and the page is available."""
        return self._page is not None and self._browser is not None

    async def safe_navigate(self, url: str, timeout_ms: int = 30_000) -> bool:
        """
        Navigate to a URL with timeout protection.

        Returns True on success, False on failure (error is emitted via emit_fn).
        Does NOT raise — safe to call in agent loops.
        """
        if not self._page:
            logger.warning('[Browser:%s] safe_navigate called but page is None', self._context_id)
            return False
        try:
            await self._page.goto(url, timeout=timeout_ms, wait_until='domcontentloaded')
            return True
        except PWError as e:
            err_str = str(e)
            logger.warning('[Browser:%s] Navigation failed to %s: %s', self._context_id, url, err_str)
            self._emit('nx:browser:nav_failed', {
                'url':        url,
                'error':      err_str,
                'context_id': self._context_id,
            })
            return False

    async def safe_screenshot(self, path: Optional[str] = None) -> Optional[bytes]:
        """Take a screenshot, returning bytes or None on failure."""
        if not self._page:
            return None
        try:
            return await self._page.screenshot(path=path, type='png')
        except PWError as e:
            logger.warning('[Browser:%s] Screenshot failed: %s', self._context_id, e)
            return None

    async def wait_for_selector(self, selector: str, timeout_ms: int = 10_000) -> bool:
        """Wait for a CSS selector. Returns True if found, False on timeout."""
        if not self._page:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except PWError:
            return False


# ── Sync convenience wrapper ──────────────────────────────────────────────────

def run_with_recovery(coro, emit_fn=None, headless=True):
    """
    Synchronous entry point: runs an async coroutine that receives a
    BrowserRecoveryContext as its first argument.

    Usage:
        def my_task(ctx):
            ctx.safe_navigate('https://example.com')

        run_with_recovery(my_task, emit_fn=sse_emit)
    """
    async def _runner():
        async with BrowserRecoveryContext(emit_fn=emit_fn, headless=headless) as ctx:
            if asyncio.iscoroutinefunction(coro):
                return await coro(ctx)
            else:
                return coro(ctx)

    return asyncio.run(_runner())
