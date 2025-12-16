# ✅ Чеклист деплою AI Document Validation

## 📋 Перед коммітом

### 1. Файли (всі створені ✅)
- [x] `prompts.py` - Промпти та типізовані причини
- [x] `ai_document_validator.py` - AI валідатор
- [x] `telegram_bot.py` - Оновлено з інтеграцією
- [x] `requirements.txt` - Додано openai та Pillow
- [x] `AI_VALIDATION_README.md` - Документація

### 2. Перевірка синтаксису (всі ✅)
- [x] `python -m py_compile prompts.py` - ОК
- [x] `python -m py_compile ai_document_validator.py` - ОК
- [x] `python -m py_compile telegram_bot.py` - ОК

### 3. База даних (зроблено ✅)
- [x] Створена таблиця `docbot.document_validations`
- [x] Додане поле `validation_status` в `docbot.documents`
- [x] Додані методи в Database клас

### 4. Змінні оточення на Render (зроблено ✅)
- [x] `OPENAI_API_KEY` - Додано
- [x] `AI_VALIDATION_ENABLED=true` - Додано

---

## 🚀 Після деплою на Render

### 1. Перевірка логів
```bash
# На Render дивіться логи
# Має бути:
✅ "Google Drive API initialized successfully"
✅ "Database connection established"
✅ "AI validation is enabled" або "AI validation is disabled"
```

### 2. Тестування AI валідації

#### Тест 1: ACCEPTED документ ✅
1. Завантажте **правильний паспорт** (читабельне фото ID-картки)
2. Очікувана поведінка:
   - Клієнт бачить: "✅ Документ успішно перевірено і завантажено!"
   - Документ зберігся на Google Drive
   - Адміни отримали нотифікацію
3. Перевірка в БД:
   ```sql
   SELECT validation_status FROM docbot.documents ORDER BY uploaded_at DESC LIMIT 1;
   -- Має бути: 'accepted'
   ```

#### Тест 2: REJECTED документ ❌
1. Завантажте **не той документ** (наприклад, водійське посвідчення)
2. Очікувана поведінка:
   - Клієнт бачить: "⚠️ Документ не пройшов перевірку: 🚗 Це водійське посвідчення (потрібен паспорт)"
   - Документ НЕ зберігся на Drive
   - Можна одразу завантажити інший файл
3. Перевірка в БД:
   ```sql
   SELECT COUNT(*) FROM docbot.documents WHERE file_name LIKE '%водійське%';
   -- Має бути: 0
   ```

#### Тест 3: UNCERTAIN документ ⚠️
1. Завантажте **розмите фото паспорта** або **скріншот**
2. Очікувана поведінка:
   - Клієнт бачить: "✅ Документ завантажено! Наш спеціаліст перевірить його найближчим часом."
   - Документ зберігся на Drive
   - Адміни отримали: "⚠️ Документ потребує перевірки"
3. Перевірка в БД:
   ```sql
   SELECT * FROM docbot.get_uncertain_documents();
   -- Має показати документ
   ```

#### Тест 4: Документи БЕЗ AI-перевірки
1. Завантажте **ЕЦП файл** (.dat, .pfx, .p12)
2. Очікувана поведінка:
   - Документ зберігся без AI-перевірки
   - Клієнт бачить: "✅ Документ успішно завантажено!"
3. Перевірка в БД:
   ```sql
   SELECT validation_status FROM docbot.documents WHERE document_type = 'ecp' ORDER BY uploaded_at DESC LIMIT 1;
   -- Має бути: NULL або 'pending'
   ```

### 3. Перевірка вартості
- Зайдіть на https://platform.openai.com/usage
- Перевірте використання API за сьогодні
- 1 перевірка ≈ $0.01

### 4. Моніторинг помилок

#### Якщо бачите помилку:
```
ERROR - Error calling GPT-4 Vision API: ...
```
**Дії:**
1. Перевірте баланс на OpenAI
2. Перевірте `OPENAI_API_KEY` в Environment Variables
3. Перевірте чи не заблокований IP Render в OpenAI

#### Якщо AI завжди повертає UNCERTAIN:
**Дії:**
1. Перевірте логи AI відповідей в БД:
   ```sql
   SELECT ai_response FROM docbot.document_validations ORDER BY validated_at DESC LIMIT 5;
   ```
2. Можливо потрібно налаштувати промпти в `prompts.py`

#### Якщо документи не перевіряються взагалі:
**Дії:**
1. Перевірте лог:
   ```
   WARNING - OPENAI_API_KEY not set - AI validation will be disabled
   ```
2. Додайте `OPENAI_API_KEY` в Render Environment Variables
3. Перезапустіть сервіс

---

## 📊 SQL запити для моніторингу

### Статистика валідацій
```sql
SELECT
    validation_status,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
FROM docbot.documents
WHERE validation_status IS NOT NULL
GROUP BY validation_status;
```

### Останні 10 перевірок
```sql
SELECT
    c.full_name,
    d.document_type,
    d.validation_status,
    v.error_code,
    d.uploaded_at
FROM docbot.documents d
JOIN docbot.clients c ON d.client_id = c.id
LEFT JOIN docbot.document_validations v ON d.id = v.document_id
WHERE d.validation_status IS NOT NULL
ORDER BY d.uploaded_at DESC
LIMIT 10;
```

### Документи що потребують перевірки
```sql
SELECT
    c.full_name,
    c.phone,
    d.document_type,
    d.file_name,
    d.drive_file_url,
    v.error_code
FROM docbot.documents d
JOIN docbot.clients c ON d.client_id = c.id
LEFT JOIN docbot.document_validations v ON d.id = v.document_id
WHERE d.validation_status = 'uncertain'
ORDER BY d.uploaded_at DESC;
```

### Найчастіші причини відхилення
```sql
SELECT
    v.error_code,
    COUNT(*) as count
FROM docbot.document_validations v
WHERE v.validation_status = 'rejected'
GROUP BY v.error_code
ORDER BY count DESC;
```

---

## 🎛️ Налаштування після деплою

### Якщо занадто багато REJECTED:
1. Відкрийте `prompts.py`
2. Зробіть промпти більш лояльними
3. Закоммітьте та задеплойте

### Якщо занадто багато UNCERTAIN:
1. Відкрийте `prompts.py`
2. Зробіть промпти більш строгими (менше UNCERTAIN → більше ACCEPTED)
3. Закоммітьте та задеплойте

### Якщо хочете вимкнути AI тимчасово:
1. На Render в Environment Variables встановіть:
   ```
   AI_VALIDATION_ENABLED=false
   ```
2. Перезапустіть сервіс
3. Всі документи будуть завантажуватись без перевірки

---

## ✅ Фінальний чеклист

- [ ] Код закоммічено в git
- [ ] Код запушено на GitHub
- [ ] Render автоматично задеплоїв
- [ ] Перевірені логи - немає помилок
- [ ] Протестовано ACCEPTED сценарій
- [ ] Протестовано REJECTED сценарій
- [ ] Протестовано UNCERTAIN сценарій
- [ ] Перевірено використання OpenAI API
- [ ] Адміни отримують нотифікації про UNCERTAIN
- [ ] База даних працює коректно

---

## 🆘 Екстрена допомога

### Повернутися до версії без AI:
```bash
git revert HEAD
git push
```

### Подивитися логи на Render:
1. Зайдіть в Dashboard → Ваш сервіс
2. Logs → Live logs
3. Шукайте "AI validation" або "GPT-4"

### Контакти для підтримки:
- OpenAI Status: https://status.openai.com/
- OpenAI Usage: https://platform.openai.com/usage
- Render Status: https://status.render.com/

---

## 🎉 Готово!

Після успішного деплою та тестування - система готова до продакшену! 🚀

**Важливо:** Перші кілька днів моніторте:
1. Логи на наявність помилок
2. Статистику валідацій в БД
3. Використання OpenAI API (щоб не було сюрпризів з рахунком)
