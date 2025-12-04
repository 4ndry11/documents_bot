# 🔐 Настройка OAuth 2.0 для Google Drive

## Почему OAuth 2.0?

**Проблема:** Service Account не имеет квоты хранения → ошибка 403
**Решение:** OAuth 2.0 = бот работает **от вашего имени** → использует вашу квоту (15 GB)

---

## Шаг 1: Создать OAuth 2.0 Credentials

1. Откройте: https://console.cloud.google.com/apis/credentials
2. Нажмите **Create Credentials** → **OAuth client ID**
3. Если просит настроить OAuth consent screen:
   - User Type: **External**
   - App name: `Telegram Document Bot`
   - User support email: ваш email
   - Developer contact: ваш email
   - Нажмите **Save and Continue**
   - Scopes: пропустить (Leave blank)
   - Test users: добавьте **свой email**
   - Нажмите **Save and Continue**
4. Вернитесь к созданию OAuth client ID:
   - Application type: **Desktop app**
   - Name: `telegram-bot-oauth`
   - Нажмите **Create**
5. **Скачайте JSON файл** (кнопка Download)
6. Переименуйте файл в `client_secret.json`

---

## Шаг 2: Получить OAuth токен (локально на вашем компьютере)

### 2.1 Установите зависимости (если ещё не установлены)

```bash
pip install google-auth-oauthlib google-api-python-client
```

### 2.2 Поместите файлы в одну папку

```
папка/
├── client_secret.json          # Скачанный OAuth credentials
├── generate_oauth_token.py     # Скрипт из репозитория
```

### 2.3 Запустите скрипт

```bash
python generate_oauth_token.py
```

**Что произойдёт:**
1. Откроется браузер
2. Войдите в свой Google аккаунт (тот, где находится папка Drive)
3. Нажмите **Разрешить** (Allow)
4. Вернитесь в терминал

**Результат:** Создастся файл `token.json`

---

## Шаг 3: Добавить токен на Render

### 3.1 Откройте файл token.json

Скопируйте **ВЕСЬ** текст из файла `token.json`. Он выглядит так:

```json
{
  "token": "ya29.a0AfB_...",
  "refresh_token": "1//0gXXX...",
  "token_uri": "https://oauth2.googleapis.com/token",
  "client_id": "123456789.apps.googleusercontent.com",
  "client_secret": "GOCSPX-XXX",
  "scopes": ["https://www.googleapis.com/auth/drive.file"]
}
```

### 3.2 Добавьте переменную окружения на Render

1. Render Dashboard → Web Service → вкладка **Environment**
2. Нажмите **Add Environment Variable**
3. Заполните:
   - **Key:** `GOOGLE_OAUTH_TOKEN`
   - **Value:** Вставьте **ВЕСЬ** содержимое token.json (весь JSON как есть)
4. Нажмите **Save Changes**

---

## Шаг 4: Проверка

Render автоматически перезапустит бот (~2 минуты).

**В логах должно быть:**
```
Using OAuth 2.0 credentials
Google Drive API initialized successfully
Bot started!
```

**Теперь загрузка файлов должна работать!** ✅

---

## Troubleshooting

### Ошибка: "Access blocked: This app's request is invalid"

**Решение:**
В OAuth consent screen добавьте свой email в **Test users**:
1. https://console.cloud.google.com/apis/credentials/consent
2. Внизу: **Test users** → **Add Users**
3. Добавьте ваш Gmail
4. Запустите `generate_oauth_token.py` снова

### Ошибка: "Token has been expired or revoked"

**Решение:**
Запустите `generate_oauth_token.py` снова и обновите `GOOGLE_OAUTH_TOKEN` на Render.

### Бот по-прежнему использует Service Account

**Проверьте:**
1. В логах Render должно быть: `Using OAuth 2.0 credentials`
2. Переменная `GOOGLE_OAUTH_TOKEN` правильно настроена (весь JSON)
3. Перезапустите сервис вручную: Settings → Manual Deploy → Deploy Latest Commit

---

## Преимущества OAuth 2.0

✅ **Использует вашу квоту** (15 GB бесплатно в Google Drive)
✅ **Файлы видны вам** сразу в вашем Drive
✅ **Нет проблем с правами доступа**
✅ **Автоматическое обновление токена** (refresh_token)

---

## Безопасность

⚠️ **НЕ ДЕЛИТЕСЬ** файлами `client_secret.json` и `token.json` ни с кем!
⚠️ Храните их локально и **НЕ загружайте на GitHub**
✅ На Render они хранятся как **environment variables** (безопасно)

---

**Готово!** 🎉 Теперь бот работает от вашего имени и использует вашу квоту Google Drive.
