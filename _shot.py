import sys
from playwright.sync_api import sync_playwright

url = "http://127.0.0.1:5057/"
out = sys.argv[1] if len(sys.argv) > 1 else "_mobile.png"
width = int(sys.argv[2]) if len(sys.argv) > 2 else 390

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": width, "height": 844}, device_scale_factor=2)
    page.goto(url, wait_until="networkidle")
    # full page
    page.screenshot(path=out, full_page=True)
    # hero-only crop
    hero = page.query_selector(".hero")
    if hero:
        hero.screenshot(path=out.replace(".png", "_hero.png"))
    b.close()
print("done", out)
