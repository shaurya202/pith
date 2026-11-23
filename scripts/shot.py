"""Click through Pith in Chrome and save screenshots.

Run while the app is already serving on port 8734:

    python scripts/shot.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs"
URL = "http://127.0.0.1:8734"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("text=Cut the pauses")
        page.screenshot(path=str(OUT / "empty.png"))

        page.get_by_role("button", name="Use the sample clip").click()
        page.wait_for_selector("video")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "imported.png"))

        page.get_by_role("button", name="Transcribe on this computer").click()
        page.wait_for_selector("text=Will cut", timeout=120_000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "editor.png"), full_page=True)

        # Fix one word, the way a person would.
        field = page.locator("#transcript input").nth(1)
        field.fill("Pith")
        field.press("Enter")
        page.wait_for_timeout(400)

        page.get_by_role("button", name="Karaoke").click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "karaoke.png"))

        page.get_by_role("button", name="Use the sample reference").click()
        page.wait_for_selector("text=not a copy of the reference", timeout=30_000)
        page.screenshot(path=str(OUT / "reference.png"))

        narrow = browser.new_page(viewport={"width": 390, "height": 844})
        narrow.goto(URL, wait_until="networkidle")
        narrow.screenshot(path=str(OUT / "mobile.png"))
        browser.close()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
