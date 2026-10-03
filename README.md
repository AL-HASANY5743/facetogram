# Facebook Group → Telegram

مشروع Python + Playwright لمراقبة منشورات كروب Facebook وإرسال المنشورات الجديدة إلى Telegram Bot.

## الفكرة

```text
Facebook Group
      ↓
Playwright / Facebook session
      ↓
استخراج المنشورات الجديدة
      ↓
منع التكرار عبر state.json
      ↓
Telegram Bot API
      ↓
Telegram Group / Channel
```

المشروع مصمم ليعمل على GitHub Actions بدون سيرفر خاص.

> ملاحظة: Facebook يغيّر واجهته باستمرار، لذلك قد تحتاج selectors إلى تحديث مستقبلاً. المشروع يستخدم Playwright ولا يعتمد على Facebook Graph API.

## 1. المتطلبات

- حساب GitHub
- Telegram Bot Token
- Telegram Chat ID
- رابط كروب Facebook
- Facebook session محفوظ بصيغة Playwright `storage_state.json`

## 2. إنشاء Facebook session

على جهازك، ثبّت Python ثم:

```bash
pip install -r requirements.txt
playwright install chromium
python tools/create_facebook_session.py
```

ستفتح نافذة Chromium. سجّل الدخول إلى Facebook يدوياً، وافتح الكروب المطلوب، ثم اضغط Enter في الطرفية.

سيتم إنشاء:

```text
secrets/facebook_storage_state.json
```

لا ترفع هذا الملف إلى GitHub.

## 3. تحويل session إلى GitHub Secret

Linux/macOS:

```bash
base64 -w 0 secrets/facebook_storage_state.json
```

Windows PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("secrets/facebook_storage_state.json"))
```

أنشئ Secret في:

`Settings → Secrets and variables → Actions`

بالاسم:

```text
FACEBOOK_STORAGE_STATE_B64
```

## 4. Telegram Secrets

أضف:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

مثال Chat ID:

```text
-1001234567890
```

## 5. إعداد الكروب

في GitHub Actions → Variables أضف:

```text
FACEBOOK_GROUP_URL
```

مثال:

```text
https://www.facebook.com/groups/example
```

يمكن أيضاً وضع الرابط في `config.json` عند التشغيل المحلي.

## 6. GitHub Actions

الملف:

```text
.github/workflows/facebook-to-telegram.yml
```

يشغّل الفحص تلقائياً كل 5 دقائق تقريباً.

GitHub يحدد الحد الأدنى للـ scheduled workflows بخمس دقائق، لكن التنفيذ قد يتأخر عند ضغط GitHub Actions. كما أن scheduled workflows تعمل على default branch. citeturn0search0turn0search2

## 7. اختبار محلي

انسخ:

```text
config.example.json
```

إلى:

```text
config.json
```

وضع:

```json
{
  "facebook_group_url": "https://www.facebook.com/groups/...",
  "telegram_bot_token": "123456:ABC...",
  "telegram_chat_id": "-1001234567890",
  "max_posts": 10
}
```

ثم:

```bash
python main.py
```

## 8. الصور والروابط

يحاول المشروع إرسال:

- نص المنشور
- رابط المنشور الأصلي
- أول صورة مناسبة موجودة في المنشور

إذا تعذر إرسال الصورة، يتم إرسال النص والرابط فقط.

## 9. منع التكرار

يتم حفظ معرفات المنشورات في:

```text
state.json
```

ويتم commit لهذا الملف تلقائياً بواسطة GitHub Actions.

لا يحتوي `state.json` على كلمة مرور أو Facebook session.

## الأمان

لا تضع أبداً:

- Facebook password
- Telegram Bot Token
- Facebook cookies/session

داخل ملفات المشروع أو commits.

استخدم GitHub Secrets.
