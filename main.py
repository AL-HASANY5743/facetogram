import base64
import binascii
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
DEBUG_DIR = ROOT / ".runtime" / "debug"


def env_or_config(config: dict, key: str, env_name: str, default=""):
    value = os.getenv(env_name)
    if value:
        return value
    return config.get(key, default)


def load_config() -> dict:
    path = ROOT / "config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sent_post_ids": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("sent_post_ids", [])
        return data
    except Exception:
        return {"sent_post_ids": []}


def save_state(state: dict):
    state["sent_post_ids"] = state.get("sent_post_ids", [])[-5000:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def decode_storage_state() -> Path:
    b64 = os.getenv("FACEBOOK_STORAGE_STATE_B64", "").strip()
    if not b64:
        local = ROOT / "secrets" / "facebook_storage_state.json"
        if local.exists():
            return local
        raise RuntimeError(
            "FACEBOOK_STORAGE_STATE_B64 is missing and local session file was not found."
        )

    # Accept a value copied from PowerShell even if accidental whitespace/newlines exist.
    b64 = re.sub(r"\s+", "", b64)
    target_dir = ROOT / ".runtime"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "facebook_storage_state.json"
    try:
        target.write_bytes(base64.b64decode(b64, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("FACEBOOK_STORAGE_STATE_B64 is not valid Base64.") from exc
    return target


def normalize_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def absolute_facebook_url(href: str) -> str:
    if href.startswith("/"):
        return "https://www.facebook.com" + href
    return href


def find_post_link(article) -> str:
    """Find common Facebook post/permalink URLs without depending on one DOM shape."""
    candidates: list[str] = []
    try:
        for a in article.locator("a[href]").all():
            href = (a.get_attribute("href") or "").strip()
            if not href:
                continue
            href = absolute_facebook_url(href)
            lower = href.lower()
            if (
                "/posts/" in lower
                or "/permalink/" in lower
                or "story_fbid=" in lower
                or "pfbid" in lower
            ):
                clean = href.split("?")[0]
                if clean not in candidates:
                    candidates.append(clean)
    except Exception:
        pass
    return candidates[0] if candidates else ""


def post_id_from_url(url: str) -> str:
    if not url:
        return ""

    patterns = [
        r"/posts/([^/?#]+)",
        r"/permalink/([^/?#]+)",
        r"story_fbid=([^&#]+)",
        r"(pfbid[A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url, flags=re.IGNORECASE)
        if m:
            return f"facebook:{m.group(1)}"

    return "url:" + url


def extract_post(article) -> dict[str, Any] | None:
    try:
        text = normalize_text(article.inner_text(timeout=5000))
    except Exception:
        return None

    link = find_post_link(article)
    if not link:
        return None

    post_id = post_id_from_url(link)
    if not post_id:
        return None

    if len(text) > 12000:
        text = text[:12000] + "\n…"

    images: list[str] = []
    try:
        for img in article.locator("img[src]").all():
            src = img.get_attribute("src") or ""
            if src.startswith("http") and "scontent" in src and src not in images:
                images.append(src)
    except Exception:
        pass

    return {
        "id": post_id,
        "url": link,
        "text": text,
        "image": images[0] if images else "",
    }


def write_debug(page, reason: str, group_url: str):
    """Write non-cookie diagnostics when Facebook returns an unexpected page."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (DEBUG_DIR / "diagnostic.txt").write_text(
            "\n".join(
                [
                    f"reason={reason}",
                    f"requested_url={group_url}",
                    f"final_url={page.url}",
                    f"title={page.title()}",
                ]
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        page.screenshot(path=str(DEBUG_DIR / "facebook-page.png"), full_page=True)
    except Exception as exc:
        print(f"[WARN] Could not save diagnostic screenshot: {exc}")


def looks_like_login_page(page) -> bool:
    url = page.url.lower()
    if "/login" in url or "checkpoint" in url or "recover" in url:
        return True

    try:
        password = page.locator('input[type="password"]').count()
        login_button = page.get_by_role("button", name=re.compile(r"log in|تسجيل الدخول", re.I)).count()
        return password > 0 and login_button > 0
    except Exception:
        return False


def scrape_posts(group_url: str, session_file: Path, max_posts: int, headless: bool):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=str(session_file),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()
        print(f"[INFO] Opening Facebook group...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        print(f"[INFO] Final URL: {page.url}")
        print(f"[INFO] Page title: {page.title()}")

        if looks_like_login_page(page):
            write_debug(page, "Facebook session appears logged out or checkpointed", group_url)
            browser.close()
            raise RuntimeError(
                "Facebook session is not logged in, or Facebook returned a login/checkpoint page. "
                "Check the diagnostic artifact 'facebook-page.png'."
            )

        if "/groups/" not in page.url.lower():
            write_debug(page, "Facebook did not stay on the requested group page", group_url)
            print("[WARN] Facebook did not remain on a /groups/ page.")

        # Let the feed render and load additional posts.
        for _ in range(5):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(1800)

        # Facebook's DOM changes frequently. Try the usual article containers first,
        # then fall back to any element containing a recognizable post URL.
        selectors = [
            '[role="article"]',
            'div[data-pagelet*="FeedUnit"]',
            'div[data-ad-preview="message"]',
        ]

        containers = []
        seen_container_count = set()
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = min(loc.count(), 200)
                if count and count not in seen_container_count:
                    print(f"[INFO] Selector {selector}: {count} elements")
                    seen_container_count.add(count)
                    containers.extend(loc.nth(i) for i in range(count))
            except Exception:
                continue

        posts: list[dict[str, Any]] = []
        seen: set[str] = set()

        for container in containers:
            if len(posts) >= max_posts:
                break
            item = extract_post(container)
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            posts.append(item)

        if not posts:
            # Diagnostic fallback: count recognizable post links on the page.
            link_count = 0
            try:
                for a in page.locator("a[href]").all():
                    href = (a.get_attribute("href") or "").lower()
                    if (
                        "/posts/" in href
                        or "/permalink/" in href
                        or "story_fbid=" in href
                        or "pfbid" in href
                    ):
                        link_count += 1
            except Exception:
                pass

            print(f"[INFO] Recognizable Facebook post links on page: {link_count}")
            write_debug(page, "No candidate posts were extracted", group_url)

        browser.close()
        return posts


def telegram_request(token: str, method: str, payload: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, data=payload, timeout=45)
    if not r.ok:
        raise RuntimeError(f"Telegram API error {r.status_code}: {r.text[:1000]}")
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API returned error: {data}")
    return data


def send_post(token: str, chat_id: str, post: dict, prefix: str, send_images: bool):
    body = post["text"].strip() or "(منشور بدون نص)"
    message = f"{prefix}{body}\n\n🔗 {post['url']}"

    if len(message) > 4000:
        message = message[:3950] + "\n…\n\n🔗 " + post["url"]

    image = post.get("image", "")
    if send_images and image:
        try:
            telegram_request(
                token,
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "photo": image,
                    "caption": message[:1020],
                },
            )
            if len(message) > 1020:
                telegram_request(
                    token,
                    "sendMessage",
                    {"chat_id": chat_id, "text": message[1020:]},
                )
            return
        except Exception as exc:
            print(f"[WARN] Image send failed, falling back to text: {exc}")

    telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
    )


def main():
    config = load_config()

    group_url = env_or_config(config, "facebook_group_url", "FACEBOOK_GROUP_URL")
    token = env_or_config(config, "telegram_bot_token", "TELEGRAM_BOT_TOKEN")
    chat_id = env_or_config(config, "telegram_chat_id", "TELEGRAM_CHAT_ID")

    if not group_url:
        raise RuntimeError("FACEBOOK_GROUP_URL is missing.")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing.")

    max_posts = int(os.getenv("MAX_POSTS", config.get("max_posts", 10)))
    headless = os.getenv("HEADLESS", str(config.get("headless", True))).lower() != "false"
    send_images = os.getenv("SEND_IMAGES", str(config.get("send_images", True))).lower() != "false"
    prefix = os.getenv("MESSAGE_PREFIX", config.get("message_prefix", ""))

    session_file = decode_storage_state()
    state = load_state()
    sent = set(state.get("sent_post_ids", []))

    print(f"[INFO] Checking: {group_url}")
    posts = scrape_posts(group_url, session_file, max_posts, headless)
    print(f"[INFO] Found {len(posts)} candidate posts.")

    new_posts = [p for p in posts if p["id"] not in sent]

    for post in reversed(new_posts):
        print(f"[INFO] Sending {post['id']}")
        send_post(token, chat_id, post, prefix, send_images)
        sent.add(post["id"])

    state["sent_post_ids"] = list(sent)[-5000:]
    save_state(state)

    print(f"[INFO] New posts sent: {len(new_posts)}")


if __name__ == "__main__":
    main()
