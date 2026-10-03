from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "secrets" / "facebook_storage_state.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="en-US"
    )
    page = context.new_page()

    print("فتح Facebook...")
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)

    print()
    print("1) سجّل الدخول إلى Facebook يدوياً.")
    print("2) افتح الكروب المطلوب وتأكد أن المنشورات تظهر.")
    print("3) ارجع إلى هذه النافذة واضغط Enter.")
    input()

    context.storage_state(path=str(OUT))
    browser.close()

print(f"تم حفظ session في: {OUT}")
print("لا ترفع هذا الملف إلى GitHub.")
