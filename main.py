import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"


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
    # Keep the state small.
    state["sent_post_ids"] = state.get("sent_post_ids", [])[-5000:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
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

    target_dir = ROOT / ".runtime"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "facebook_storage_state.json"
    target.write_bytes(base64.b64decode(b64))
    return target


def normalize_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def find_post_link(article) -> str:
    candidates = []
    try:
        links = article.locator("a").all()
        for a in links:
            href = a.get_attribute("href") or ""
            if not href:
                continue
            if any(x in href for x in ("/posts/", "/permalink/", "/groups/")):
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href
                candidates.append(href.split("?")[0])
    except Exception:
        pass
    return candidates[0] if candidates else ""


def post_id_from_url(url: str) -> str:
    if not url:
        return ""
    # Stable enough for common Facebook group post URLs.
    m = re.search(r"/posts/([^/?#]+)", url)
    if m:
        return "posts:" + m.group(1)
    m = re.search(r"/permalink/([^/?#]+)", url)
    if m:
        return "permalink:" + m.group(1)
    # Fallback to normalized URL.
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

    # Avoid treating huge page-level containers as posts.
    if len(text) > 12000:
        text = text[:12000] + "\n…"

    images = []
    try:
        for img in article.locator("img").all():
            src = img.get_attribute("src") or ""
            if src.startswith("http") and "scontent" in src:
                images.append(src)
    except Exception:
        pass

    return {
        "id": post_id,
        "url": link,
        "text": text,
        "image": images[0] if images else ""
    }


def scrape_posts(group_url: str, session_file: Path, max_posts: int, headless: bool):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
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
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_timeout(5000)
            page.locator('[role="article"]').first.wait_for(timeout=15000)
        except PlaywrightTimeoutError:
            pass

        # Scroll a little to let Facebook populate the feed.
        for _ in range(3):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(1500)

        articles = page.locator('[role="article"]').all()
        posts = []
        seen = set()

        for article in articles:
            if len(posts) >= max_posts:
                break
            item = extract_post(article)
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            posts.append(item)

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
    body = post["text"].strip()
    if not body:
        body = "(منشور بدون نص)"

    message = f"{prefix}{body}\n\n🔗 {post['url']}"

    # Telegram text messages have a 4096 character limit.
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
            # If the text was too long for a caption, send the remainder.
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

    max_posts = int(
        os.getenv("MAX_POSTS", config.get("max_posts", 10))
    )
    headless = os.getenv(
        "HEADLESS",
        str(config.get("headless", True))
    ).lower() != "false"
    send_images = os.getenv(
        "SEND_IMAGES",
        str(config.get("send_images", True))
    ).lower() != "false"
    prefix = os.getenv(
        "MESSAGE_PREFIX",
        config.get("message_prefix", "")
    )

    session_file = decode_storage_state()
    state = load_state()
    sent = set(state.get("sent_post_ids", []))

    print(f"[INFO] Checking: {group_url}")
    posts = scrape_posts(group_url, session_file, max_posts, headless)
    print(f"[INFO] Found {len(posts)} candidate posts.")

    new_posts = [p for p in posts if p["id"] not in sent]

    # Process oldest-to-newest among the discovered feed items.
    for post in reversed(new_posts):
        print(f"[INFO] Sending {post['id']}")
        send_post(token, chat_id, post, prefix, send_images)
        sent.add(post["id"])

    state["sent_post_ids"] = list(sent)[-5000:]
    save_state(state)

    print(f"[INFO] New posts sent: {len(new_posts)}")


if __name__ == "__main__":
    main()
