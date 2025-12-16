# 🤖 AI Document Validation System

## Огляд

Система автоматичної перевірки документів за допомогою GPT-4 Vision (GPT-4o) від OpenAI.

## 📁 Структура файлів

- **`prompts.py`** - Промпти для AI та типізовані причини відхилення
- **`ai_document_validator.py`** - Основний модуль AI-валідації
- **`telegram_bot.py`** - Інтеграція в Telegram бот (оновлено)

## 🔧 Налаштування

### 1. Змінні оточення (вже додані на Render)

```bash
OPENAI_API_KEY=sk-...                    # API ключ OpenAI
AI_VALIDATION_ENABLED=true               # Увімкнути/вимкнути AI валідацію
```

### 2. База даних (вже створена таблиця)

```sql
CREATE TABLE docbot.document_validations (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES docbot.documents(id),
    validation_status VARCHAR(20),
    error_code VARCHAR(50),
    ai_response JSONB,
    validated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE docbot.documents
ADD COLUMN validation_status VARCHAR(20) DEFAULT 'pending';
```

### 3. Залежності (вже додані в requirements.txt)

```
openai>=1.0.0
Pillow>=10.0.0
```

## 🚀 Як це працює

### Процес перевірки документа:

```
1. Клієнт завантажує документ
   ↓
2. Бот завантажує файл в temp
   ↓
3. AI перевіряє документ (GPT-4o Vision)
   - Етап 1: Перевірка якості
   - Етап 2: Перевірка типу документа
   ↓
4. Результат:
   ├── ✅ ACCEPTED → Зберігаємо на Drive + повідомляємо клієнта
   ├── ❌ REJECTED → НЕ зберігаємо + просимо перезавантажити
   └── ⚠️ UNCERTAIN → Зберігаємо на Drive + сповіщаємо адміна
```

### Логіка 3 статусів:

#### ✅ ACCEPTED (Документ прийнято)
- AI впевнений що документ правильний
- Документ зберігається на Google Drive
- Клієнт бачить: "✅ Документ успішно перевірено і завантажено!"
- Адміни отримують нотифікацію про новий документ

#### ❌ REJECTED (Документ відхилено)
- AI впевнений що це НЕ той документ або погана якість
- Документ НЕ зберігається на Drive
- Клієнт бачить причину з [prompts.py](prompts.py):
  ```
  ⚠️ Документ не пройшов перевірку:

  📷 Зображення не чітке або розмите

  Будь ласка, перегляньте інструкцію та завантажте правильний документ.
  ```

#### ⚠️ UNCERTAIN (Потрібна перевірка)
- AI не впевнений (можливо підроблений, частково читабельний тощо)
- Документ зберігається на Drive
- Клієнт бачить: "✅ Документ завантажено! Наш спеціаліст перевірить його найближчим часом."
- Адміни отримують спеціальне сповіщення:
  ```
  ⚠️ Документ потребує перевірки

  👤 Клієнт: [ПІБ]
  📱 Телефон: [phone]
  📄 Тип документа: [тип]
  🤖 AI сумнівається в документі

  📁 [Переглянути документ]
  📂 [Папка клієнта]
  ```

## 📋 Типи документів що перевіряються

| Документ | Перевірка AI | Промпт |
|-----------|-------------|---------|
| `passport` | ✅ Так | Паспорт + ІПН (лояльно) |
| `registration` | ✅ Так | Склад сім'ї |
| `credit_contracts` | ✅ Так | Кредитні договори |
| `bank_statements` | ✅ Так | Виписки |
| `expenses` | ✅ Так | Витрати (максимально лояльно) |
| `debt_certificates` | ✅ Так | Заборгованості |
| `ecpass` | ❌ Ні | Текстовий пароль |
| `ecp` | ❌ Ні | Файл ключа |
| `story` | ❌ Ні | Word документ |
| `workbook` | ❌ Ні | Опціонально |
| `family_income` | ❌ Ні | Опціонально |
| `executive` | ❌ Ні | Опціонально |

## 🎯 Типізовані причини відхилення

Усі причини відхилення зберігаються в [prompts.py](prompts.py):

### Проблеми з якістю:
- `poor_quality` - 📷 Зображення не чітке або розмите
- `low_resolution` - 📷 Занадто низька роздільна здатність
- `glare` - 💡 Документ засвітлений або є блиски
- `cut_off` - ✂️ Документ обрізаний, видно не всі поля
- `unreadable` - 🔍 Текст не читається
- `damaged_file` - 🔧 Файл пошкоджений або не відкривається

### Проблеми з типом:
- `wrong_document` - ❌ Підвантажено не той документ
- `wrong_country` - 🌍 Документ іншої країни
- `foreign_passport` - 🛂 Це закордонний паспорт (потрібен внутрішній)
- `drivers_license` - 🚗 Це водійське посвідчення (потрібен паспорт)

### Невизначеність:
- `uncertain` - ⚠️ Документ потребує додаткової перевірки

## 💰 Вартість

При обсязі 100 клієнтів/місяць × 8 документів = 800 перевірок:
- **Приблизно $8-10/місяць**
- ~$0.01 за перевірку одного документа

## 🔍 Моніторинг

### Перегляд результатів валідації в БД:

```sql
-- Статистика по валідаціям
SELECT
    validation_status,
    COUNT(*)
FROM docbot.documents
WHERE validation_status IS NOT NULL
GROUP BY validation_status;

-- Документи що потребують перевірки
SELECT * FROM docbot.get_uncertain_documents();

-- Детальна інформація про валідацію
SELECT
    d.file_name,
    d.document_type,
    d.validation_status,
    v.error_code,
    v.ai_response,
    v.validated_at
FROM docbot.documents d
LEFT JOIN docbot.document_validations v ON d.id = v.document_id
WHERE d.validation_status = 'uncertain';
```

### Нотифікації адмінам:

```sql
-- Переглянути всі нотифікації
SELECT * FROM docbot.notifications_log
WHERE notification_type IN (
    'document_rejected',
    'document_uploaded_accepted',
    'document_uploaded_uncertain'
)
ORDER BY created_at DESC;
```

## 🛠️ Налаштування промптів

Усі промпти знаходяться в [prompts.py](prompts.py). Щоб змінити поведінку AI:

1. Відредагуйте промпт в `DOCUMENT_PROMPTS[тип_документа]`
2. При потребі додайте нові `error_code` в `REJECTION_REASONS`
3. Перезапустіть бот

### Приклад: Зробити перевірку більш строгою

```python
# В prompts.py змініть:
ВАЖЛИВО: Будь ЛОЯЛЬНИМ
# на:
ВАЖЛИВО: Будь СТРОГИМ
```

### Приклад: Додати новий тип помилки

```python
REJECTION_REASONS = {
    # ...існуючі...
    'expired_document': '⏰ Документ прострочений'
}
```

## 🧪 Тестування

### Локальне тестування:

```python
from ai_document_validator import validator

# Перевірка документа
result = validator.validate_document(
    file_path='path/to/test_passport.jpg',
    document_type='passport'
)

print(f"Status: {result.status}")
print(f"Error code: {result.error_code}")
print(f"User message: {result.get_user_message()}")
```

### Тестування через бота:

1. Завантажте документ через бот
2. Перевірте лог в консолі:
   ```
   INFO - AI validation result: accepted (error_code: None)
   ```
3. Перевірте БД:
   ```sql
   SELECT * FROM docbot.document_validations ORDER BY validated_at DESC LIMIT 1;
   ```

## 🚨 Усунення несправностей

### AI валідація не працює:

1. Перевірте змінні оточення:
   ```bash
   echo $OPENAI_API_KEY
   echo $AI_VALIDATION_ENABLED
   ```

2. Перевірте логи:
   ```
   ERROR - Error calling GPT-4 Vision API: ...
   ```

3. Перевірте баланс OpenAI API

### Документи завжди UNCERTAIN:

- Перевірте промпти в [prompts.py](prompts.py)
- Можливо потрібно зробити промпти більш лояльними

### Помилка "ai_validator not found":

- Перевірте що файли `prompts.py` та `ai_document_validator.py` в одній папці з `telegram_bot.py`
- Перезапустіть бот

## 📊 База даних

### Нові таблиці:

**`document_validations`** - історія всіх AI-перевірок:
- `document_id` - ID документа
- `validation_status` - accepted/rejected/uncertain
- `error_code` - код помилки з REJECTION_REASONS
- `ai_response` - повна відповідь від AI (JSONB)
- `validated_at` - час перевірки

### Нові поля:

**`documents.validation_status`** - поточний статус валідації документа

### Нові методи Database:

```python
db.save_document_validation(document_id, validation_status, error_code, ai_response)
db.get_document_validation(document_id)
db.update_document_validation_status(document_id, validation_status)
db.get_uncertain_documents()  # Документи що потребують перевірки
```

## 🎛️ Вимкнення AI валідації

Якщо потрібно тимчасово вимкнути AI перевірку:

```bash
# На Render в Environment Variables:
AI_VALIDATION_ENABLED=false
```

Або видалити `OPENAI_API_KEY` - тоді валідація автоматично вимкнеться.

## 📝 Приклади промптів

### Паспорт (ЛОЯЛЬНИЙ):
```
ВАЖЛИВО: Будь ЛОЯЛЬНИМ. Клієнт може завантажити паспорт і ІПН окремо або разом.

✅ ПРИЙНЯТИ якщо ЦЕ:
- Будь-яка частина українського паспорта
- Картка ІПН
- Фото екрану з паспортом/ІПН
- Навіть якщо не всі сторінки
```

### Витрати (МАКСИМАЛЬНО ЛОЯЛЬНИЙ):
```
ВАЖЛИВО: Будь МАКСИМАЛЬНО ЛОЯЛЬНИМ. Приймай ВСІ чеки, квитанції.

✅ ПРИЙНЯТИ майже ВСЕ що схоже на чек/квитанцію/оплату
```

## ✅ Готово до продакшену!

Система повністю інтегрована та готова до використання. Після деплою на Render AI-перевірка автоматично почне працювати для всіх нових документів.
