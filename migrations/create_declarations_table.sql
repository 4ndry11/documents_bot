-- Створення таблиці для анкет декларацій
CREATE TABLE IF NOT EXISTS docbot.declarations (
    id SERIAL PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES docbot.clients(id) ON DELETE CASCADE,

    -- Відповіді на питання
    email_password TEXT,                    -- Q1: Email та пароль
    living_address_2022_2025 TEXT,          -- Q2: Адреса проживання 2022-2025
    registration_change TEXT,               -- Q3: Зміна прописки (необов'язкове)
    property_alienation_self TEXT,          -- Q4: Відчуження майна (ви)
    property_alienation_family TEXT,        -- Q5: Відчуження майна (сім'я)
    family_vehicles TEXT,                   -- Q6: Транспортні засоби
    corporate_rights TEXT,                  -- Q7: Корпоративні права/акції
    crypto_foreign_credits TEXT,            -- Q8: Кредити в крипті/валюті
    specific_bank_credits TEXT,             -- Q9: Кредити Ощадбанк/OTP/Mono
    online_betting TEXT,                    -- Q10: Онлайн ставки
    bank_installments TEXT,                 -- Q11: Розстрочки в банках
    creditor_address TEXT,                  -- Q12: Адреса для кредиторів
    housing_owner TEXT,                     -- Q13: Власник житла
    marriage_transactions TEXT,             -- Q14: Купівля/продаж у шлюбі
    alienation_documents TEXT,              -- Q15: JSON array з файлами (необов'язкове)
    vehicle_power_of_attorney TEXT,         -- Q16: Авто по довіреності (необов'язкове)
    alimony_info TEXT,                      -- Q17: Аліменти (необов'язкове)

    -- Метадані
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR(50) DEFAULT 'in_progress',  -- in_progress, completed

    CONSTRAINT unique_client_declaration UNIQUE (client_id)
);

-- Індекс для швидкого пошуку по client_id
CREATE INDEX IF NOT EXISTS idx_declarations_client_id ON docbot.declarations(client_id);
CREATE INDEX IF NOT EXISTS idx_declarations_status ON docbot.declarations(status);
