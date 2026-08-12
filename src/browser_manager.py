import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Browser, Playwright

logger = logging.getLogger(__name__)

class BrowserSessionManager:
    _playwright: Optional[Playwright] = None
    _browser: Optional[Browser] = None

    @classmethod
    def get_browser(cls) -> Browser:
        if cls._browser is None:
            cls._playwright = sync_playwright().__enter__()
            cls._browser = cls._playwright.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"]
            )
            logger.info("BrowserSessionManager: Chromium browser started")
        return cls._browser

    @classmethod
    def close_all(cls) -> None:
        if cls._browser:
            try:
                cls._browser.close()
            except Exception:
                pass
            cls._browser = None
        if cls._playwright:
            try:
                cls._playwright.stop()
            except Exception:
                pass
            cls._playwright = None
        logger.info("BrowserSessionManager: Browser session closed")
