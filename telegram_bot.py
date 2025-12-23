"""
Telegram Bot для збору документів клієнтів
Все в одному файлі
"""
import os
import logging
import tempfile
import base64
import json
import pytz
from datetime import datetime, timezone
from io import BytesIO
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# Database
import psycopg2
from psycopg2.extras import RealDictCursor

# Google Drive
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

# AI Document Validator
from ai_document_validator import validator as ai_validator

# ============================================================================
# КОНФІГУРАЦІЯ
# ============================================================================

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
NOTIFICATION_BOT_TOKEN = os.getenv('NOTIFICATION_BOT_TOKEN')

# Адмін-панель (через Deep Link)
ADMIN_SECRET_CODE = os.getenv('ADMIN_SECRET_CODE', 'f7T9vQ1111wLp2Gx8Z')  # Секретний код для адмінів
# Шлях до файлу адмінів (використовує persistent disk на Render якщо доступний)
ADMIN_FILE_PATH = os.getenv('ADMIN_FILE_PATH', '/var/data')  # Render persistent disk
ADMIN_FILE = os.path.join(ADMIN_FILE_PATH, 'admins.txt') if os.path.exists(ADMIN_FILE_PATH) else 'admins.txt'

# Database
DATABASE_URL = os.getenv('DATABASE_URL')

# Google Drive
ROOT_FOLDER_ID = os.getenv('ROOT_FOLDER_ID')
GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')
GOOGLE_CREDENTIALS_BASE64 = os.getenv('GOOGLE_CREDENTIALS_BASE64')
DRIVE_OWNER_EMAIL = os.getenv('DRIVE_OWNER_EMAIL')  # Email владельца Drive (ваш Gmail)
GOOGLE_OAUTH_TOKEN = os.getenv('GOOGLE_OAUTH_TOKEN')  # OAuth токен (JSON string)

# Settings
REMINDER_DAYS = int(os.getenv('REMINDER_DAYS', 3))

# Підпапки на Drive
SUBFOLDERS = {
    'credit': 'Кредитні договори',
    'personal': 'Особисті документи',
    'declaration': 'Декларація',
    'expenses_confirmation': 'Підвердження витрат',
    'debt_confirmation': 'Підвердження заборгованості',
    'additional': 'Додаткові документи'
}

# ============================================================================
# ТИПИ ДОКУМЕНТІВ (СТРОГО ОБОЗНАЧЕНІ)
# ============================================================================

DOCUMENT_TYPES = {
    'ecpass': {
        'name': 'Пароль від ЕЦП',
        'emoji': '🔐',
        'folder': 'personal',
        'required': True,
        'is_text': True
    },
    'ecp': {
        'name': 'ЕЦП (електронний цифровий підпис)',
        'short': 'ЕЦП',
        'emoji': '📜',
        'folder': 'personal',
        'required': True,
        'video': 'https://www.youtube.com/watch?v=S5OTYY9hyQY'
    },
    'passport': {
        'name': 'Сканкопія паспорта та РНОКПП (ІПН)',
        'short': 'Паспорт',
        'emoji': '📕',
        'folder': 'personal',
        'required': True,
        'video': 'https://www.youtube.com/shorts/QMyoYlybUOk'
    },
    'registration': {
        'name': 'Витяг з реєстру територіальної громади',
        'short': 'Склад сім\'ї',
        'description': 'Довідка про склад сім\'ї (витяг з реєстру територіальної громади)',
        'emoji': '🏠',
        'folder': 'personal',
        'required': True,
        'video': 'https://www.youtube.com/shorts/9C5XE1gpGNM'
    },
    'workbook': {
        'name': 'Копія трудової книжки',
        'short': 'Трудова книжка',
        'emoji': '📗',
        'folder': 'personal',
        'required': False,
        'video': 'https://www.youtube.com/shorts/xB-xZUD_yu8'
    },
    'credit_contracts': {
        'name': 'Кредитні договори',
        'short': 'Кредитні договори',
        'emoji': '📑',
        'folder': 'credit',
        'required': True,
        'multiple': True,
        'video': 'https://www.youtube.com/shorts/vhOq-iw_B0A'
    },
    'bank_statements': {
        'name': 'Виписки про залишок коштів на рахунках',
        'short': 'Виписки',
        'emoji': '🏦',
        'folder': 'personal',
        'required': True,
        'video': 'https://www.youtube.com/shorts/5yzLPrDhImo'
    },
    'expenses': {
        'name': 'Підтвердження витрат за останні місяці',
        'short': 'Витрати',
        'emoji': '💰',
        'folder': 'expenses_confirmation',
        'required': True,
        'video': 'https://www.youtube.com/shorts/YfYkxGiyATo'
    },
    'story': {
        'name': 'Ваша історія (у форматі Word)',
        'short': 'Історія',
        'emoji': '📝',
        'folder': 'personal',
        'required': True,
        'video': 'https://www.youtube.com/shorts/KkFbbSkF6Jg'
    },
    'family_income': {
        'name': 'Доходи членів сім\'ї (довідка з податкової)',
        'short': 'Доходи членів сім\'ї',
        'emoji': '💵',
        'folder': 'personal',
        'required': False,
        'video': 'https://www.youtube.com/watch?v=fqhRCe-cMAc'
    },
    'debt_certificates': {
        'name': 'Довідки про стан заборгованості',
        'short': 'Заборгованості',
        'emoji': '📋',
        'folder': 'debt_confirmation',
        'required': True
    },
    'executive': {
        'name': 'Виписки по виконавчих провадженнях',
        'short': 'Виконавчі',
        'emoji': '⚖️',
        'folder': 'personal',
        'required': False
    },
    'additional_docs': {
        'name': 'Додаткові документи',
        'short': 'Додаткові документи',
        'emoji': '📎',
        'folder': 'additional',
        'required': False,
        'skip_ai_validation': True,
        'requires_custom_name': True
    }
}

REQUIRED_DOCUMENTS = [key for key, val in DOCUMENT_TYPES.items() if val.get('required', False)]

# ============================================================================
# ПИТАННЯ АНКЕТИ ДЕКЛАРАЦІЇ
# ============================================================================

DECLARATION_QUESTIONS = [
    {
        'key': 'email_password',
        'emoji': '📧',
        'question': 'Ваша електронна пошта та пароль яку вказували під час оформлення кредитів у разі втрати доступу - до діючої.',
        'required': True
    },
    {
        'key': 'living_address_2022_2025',
        'emoji': '🏠',
        'question': 'Адреса фактичного місця проживання з 2022 по 2025 рік',
        'hint': 'Якщо фактично 2022-2024 не проживали за місцем реєстрації, напишіть адреси, де проживали по роках конкретно; та адресу місця проживання за 2025 рік.',
        'required': True
    },
    {
        'key': 'registration_change',
        'emoji': '📍',
        'question': 'Якщо була зміна адреси реєстрації (прописки) у 2022–2025 то вкажіть стару адресу та дату зміни',
        'required': False
    },
    {
        'key': 'property_alienation_self',
        'emoji': '🏡',
        'question': 'Опишіть чи було відчуження (дарування, продаж і т.д.) майна у вас у 2022–2025 роках. Якщо було - вкажіть деталі (що, коли, кому). Якщо не було - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'property_alienation_family',
        'emoji': '👨‍👩‍👧',
        'question': 'Опишіть чи було відчуження майна у членів вашої сім\'ї у 2022–2025 роках. Якщо було - вкажіть деталі (хто, що, коли). Якщо не було - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'family_vehicles',
        'emoji': '🚗',
        'question': 'Опишіть чи є у членів сім\'ї транспортні засоби у власності. Якщо так - вкажіть марку, рік, на кого зареєстровано. Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'corporate_rights',
        'emoji': '📊',
        'question': 'Опишіть чи є у вас зараз або були у 2022-2024 роках корпоративні права, акції, цінні папери у власності. Якщо так - вкажіть деталі. Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'crypto_foreign_credits',
        'emoji': '💱',
        'question': 'Опишіть чи є у вас кредити у криптовалюті або іноземній валюті. Якщо так - вкажіть деталі (сума, валюта, кредитор). Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'specific_bank_credits',
        'emoji': '💱',
        'question': 'Опишіть чи є у вас кредит в АТ Ощадбанку, OTP bank або розстрочки від Monobank. Якщо так - вкажіть де саме та суму. Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'online_betting',
        'emoji': '🎲',
        'question': 'Опишіть чи ставили ви коли-небудь ставки онлайн. Якщо так - вкажіть де та коли. Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'bank_installments',
        'emoji': '💳',
        'question': 'Опишіть чи були у вас розстрочки в банках. Якщо так - вкажіть в яких банках та на що. Якщо ні - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'creditor_address',
        'emoji': '📌',
        'question': 'Яка адреса вказувалася кредиторам (не нова, не чиста)?',
        'required': True
    },
    {
        'key': 'housing_owner',
        'emoji': '🏠',
        'question': 'Хто є власником житла, в якому ви зареєстровані/проживаєте?',
        'required': True
    },
    {
        'key': 'marriage_transactions',
        'emoji': '💍',
        'question': 'Опишіть чи куплялося/продавалося щось у шлюбі. Якщо так - вкажіть що саме та коли. Якщо ні або не перебуваєте в шлюбі - напишіть "Ні".',
        'required': True
    },
    {
        'key': 'alienation_documents',
        'emoji': '📑',
        'question': 'Якщо було відчуження майна — завантажте документи (договори купівлі/продажу, дарування тощо)',
        'type': 'files',
        'required': False
    },
    {
        'key': 'vehicle_power_of_attorney',
        'emoji': '🚘',
        'question': 'Якщо авто досі зареєстроване на вас, але продане по довіреності - напишіть про це.',
        'required': False
    },
    {
        'key': 'alimony_info',
        'emoji': '❗',
        'question': 'Опишіть чи отримуєте аліменти на дітей/сплачуєте аліменти/маєте заборгованість по аліментах. Якщо так - вкажіть деталі. Якщо ні - напишіть "Ні". Можете пропустити це питання.',
        'required': False
    }
]

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================================
# ADMIN FILE MANAGEMENT
# ============================================================================

def load_admins():
    """Завантажити список адмінів з файлу"""
    if not os.path.exists(ADMIN_FILE):
        return set()
    try:
        with open(ADMIN_FILE, 'r') as f:
            return {int(line.strip()) for line in f if line.strip()}
    except Exception as e:
        logger.error(f"Error loading admins: {e}")
        return set()

def save_admin(telegram_id):
    """Додати адміна до файлу"""
    admins = load_admins()
    if telegram_id not in admins:
        admins.add(telegram_id)
        try:
            with open(ADMIN_FILE, 'w') as f:
                for admin_id in admins:
                    f.write(f"{admin_id}\n")
            logger.info(f"Admin {telegram_id} saved to file")
            return True
        except Exception as e:
            logger.error(f"Error saving admin: {e}")
            return False
    return True

# ============================================================================
# БАЗА ДАНИХ (PostgreSQL)
# ============================================================================

class Database:
    def __init__(self):
        self.conn = None
        self._connect()

    def _connect(self):
        try:
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass
            self.conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
            self.conn.autocommit = True
            logger.info("Database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    def _ensure_connection(self):
        try:
            if self.conn is None or self.conn.closed:
                logger.warning("Database connection lost, reconnecting...")
                self._connect()
            else:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning(f"Database connection check failed: {e}, reconnecting...")
            self._connect()

    def execute(self, query, params=None, fetch=False):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._ensure_connection()
                with self.conn.cursor() as cur:
                    cur.execute(query, params or ())
                    if fetch:
                        return cur.fetchall() if cur.description else None
                    return cur.rowcount
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                logger.error(f"Database error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    self._connect()
                else:
                    raise
            except Exception as e:
                logger.error(f"Database error: {e}")
                raise

    # Clients
    def create_client(self, telegram_id, full_name, phone):
        query = """
            INSERT INTO docbot.clients (telegram_id, full_name, phone)
            VALUES (%s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE
            SET full_name = EXCLUDED.full_name,
                phone = EXCLUDED.phone,
                last_activity = CURRENT_TIMESTAMP
            RETURNING *
        """
        result = self.execute(query, (telegram_id, full_name, phone), fetch=True)
        return result[0] if result else None

    def get_client_by_telegram_id(self, telegram_id):
        query = "SELECT * FROM docbot.clients WHERE telegram_id = %s"
        result = self.execute(query, (telegram_id,), fetch=True)
        return result[0] if result else None

    def get_client_by_phone(self, phone):
        query = "SELECT * FROM docbot.clients WHERE phone = %s"
        result = self.execute(query, (phone,), fetch=True)
        return result[0] if result else None

    def get_client_by_id(self, client_id):
        query = "SELECT * FROM docbot.clients WHERE id = %s"
        result = self.execute(query, (client_id,), fetch=True)
        return result[0] if result else None

    def update_client_drive_folder(self, client_id, folder_id, folder_url):
        query = """
            UPDATE docbot.clients
            SET drive_folder_id = %s, drive_folder_url = %s, last_activity = CURRENT_TIMESTAMP
            WHERE id = %s
        """
        self.execute(query, (folder_id, folder_url, client_id))

    def update_client_status(self, client_id, status):
        query = "UPDATE docbot.clients SET status = %s, last_activity = CURRENT_TIMESTAMP WHERE id = %s"
        self.execute(query, (status, client_id))

    def update_last_activity(self, client_id):
        query = "UPDATE docbot.clients SET last_activity = CURRENT_TIMESTAMP WHERE id = %s"
        self.execute(query, (client_id,))

    # Documents
    def add_document(self, client_id, document_type, file_name, drive_file_id, drive_file_url, file_size, uploaded_by_admin_id=None):
        query = """
            INSERT INTO docbot.documents (client_id, document_type, file_name, drive_file_id, drive_file_url, file_size, uploaded_by_admin_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        result = self.execute(query, (client_id, document_type, file_name, drive_file_id, drive_file_url, file_size, uploaded_by_admin_id), fetch=True)
        return result[0]['id'] if result else None

    def get_uploaded_types(self, client_id):
        query = """
            SELECT DISTINCT document_type, COUNT(*) as count
            FROM docbot.documents
            WHERE client_id = %s
            GROUP BY document_type
        """
        result = self.execute(query, (client_id,), fetch=True)
        return {row['document_type']: row['count'] for row in result} if result else {}

    def get_documents_by_client(self, client_id):
        query = "SELECT * FROM docbot.documents WHERE client_id = %s ORDER BY uploaded_at DESC"
        return self.execute(query, (client_id,), fetch=True)

    # EC Passwords
    def save_ec_password(self, client_id, password):
        query = """
            INSERT INTO docbot.ec_passwords (client_id, password)
            VALUES (%s, %s)
            ON CONFLICT (client_id) DO UPDATE
            SET password = EXCLUDED.password, created_at = CURRENT_TIMESTAMP
            RETURNING id
        """
        result = self.execute(query, (client_id, password), fetch=True)
        return result[0]['id'] if result else None

    def get_ec_password(self, client_id):
        query = "SELECT password FROM docbot.ec_passwords WHERE client_id = %s ORDER BY created_at DESC LIMIT 1"
        result = self.execute(query, (client_id,), fetch=True)
        return result[0]['password'] if result else None

    # Notifications
    def log_notification(self, client_id, notification_type, message, admin_telegram_id=None):
        query = "INSERT INTO docbot.notifications_log (client_id, notification_type, message, admin_telegram_id) VALUES (%s, %s, %s, %s)"
        self.execute(query, (client_id, notification_type, message, admin_telegram_id))

    def get_inactive_clients(self):
        """Получить клиентов неактивных 3+ дня"""
        query = """
            SELECT * FROM docbot.clients
            WHERE status = 'in_progress'
            AND (NOW() AT TIME ZONE 'UTC' - last_activity) >= INTERVAL '3 days'
        """
        return self.execute(query, fetch=True)

    # Reminders
    def log_reminder(self, client_id, days_inactive):
        """Записати відправлене нагадування"""
        query = """
            INSERT INTO docbot.reminders_log (client_id, days_inactive, sent_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
        """
        self.execute(query, (client_id, days_inactive))

    def get_last_reminder(self, client_id):
        """Отримати останнє нагадування для клієнта"""
        query = """
            SELECT * FROM docbot.reminders_log
            WHERE client_id = %s
            ORDER BY sent_at DESC
            LIMIT 1
        """
        result = self.execute(query, (client_id,), fetch=True)
        return result[0] if result else None

    def create_reminders_table(self):
        """Створити таблицю для логування нагадувань"""
        query = """
            CREATE TABLE IF NOT EXISTS docbot.reminders_log (
                id SERIAL PRIMARY KEY,
                client_id INTEGER REFERENCES docbot.clients(id) ON DELETE CASCADE,
                days_inactive INTEGER NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        self.execute(query)

    # Declarations
    def get_or_create_declaration(self, client_id):
        """Отримати існуючу декларацію або створити нову"""
        query = "SELECT * FROM docbot.declarations WHERE client_id = %s"
        result = self.execute(query, (client_id,), fetch=True)
        if result:
            return result[0]

        # Створюємо нову декларацію
        query = "INSERT INTO docbot.declarations (client_id) VALUES (%s) RETURNING *"
        result = self.execute(query, (client_id,), fetch=True)
        return result[0] if result else None

    def update_declaration_answer(self, client_id, field_name, answer):
        """Оновити відповідь на питання в декларації"""
        query = f"UPDATE docbot.declarations SET {field_name} = %s WHERE client_id = %s"
        self.execute(query, (answer, client_id))

    def complete_declaration(self, client_id):
        """Позначити декларацію як завершену"""
        query = "UPDATE docbot.declarations SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE client_id = %s"
        self.execute(query, (client_id,))

    def get_declaration(self, client_id):
        """Отримати декларацію клієнта"""
        query = "SELECT * FROM docbot.declarations WHERE client_id = %s"
        result = self.execute(query, (client_id,), fetch=True)
        return result[0] if result else None

    # Document Validations (AI)
    def save_document_validation(self, document_id, validation_status, ai_response):
        """Зберегти результат AI-валідації документа"""
        query = """
            INSERT INTO docbot.document_validations
            (document_id, validation_status, ai_response, validated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            RETURNING id
        """
        result = self.execute(
            query,
            (document_id, validation_status, json.dumps(ai_response) if ai_response else None),
            fetch=True
        )
        return result[0]['id'] if result else None

    def get_document_validation(self, document_id):
        """Отримати результат валідації документа"""
        query = "SELECT * FROM docbot.document_validations WHERE document_id = %s ORDER BY validated_at DESC LIMIT 1"
        result = self.execute(query, (document_id,), fetch=True)
        return result[0] if result else None

    def update_document_validation_status(self, document_id, validation_status):
        """Оновити статус валідації документа"""
        query = """
            UPDATE docbot.documents
            SET validation_status = %s
            WHERE id = %s
        """
        self.execute(query, (validation_status, document_id))

    def get_uncertain_documents(self):
        """Отримати всі документи зі статусом UNCERTAIN що потребують ручної перевірки"""
        query = """
            SELECT d.*, c.full_name, c.phone, c.telegram_id
            FROM docbot.documents d
            JOIN docbot.clients c ON d.client_id = c.id
            WHERE d.validation_status = 'uncertain'
            ORDER BY d.uploaded_at DESC
        """
        return self.execute(query, fetch=True)

# ============================================================================
# GOOGLE DRIVE
# ============================================================================

class DriveManager:
    def __init__(self):
        try:
            # Приоритет: OAuth > Service Account
            if GOOGLE_OAUTH_TOKEN:
                logger.info("Using OAuth 2.0 credentials")
                token_data = json.loads(GOOGLE_OAUTH_TOKEN)
                credentials = Credentials(
                    token=token_data.get('token'),
                    refresh_token=token_data.get('refresh_token'),
                    token_uri=token_data.get('token_uri'),
                    client_id=token_data.get('client_id'),
                    client_secret=token_data.get('client_secret'),
                    scopes=token_data.get('scopes')
                )
            elif GOOGLE_CREDENTIALS_BASE64:
                logger.info("Using base64 Service Account credentials")
                creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64)
                creds_dict = json.loads(creds_json)
                credentials = service_account.Credentials.from_service_account_info(
                    creds_dict,
                    scopes=['https://www.googleapis.com/auth/drive']
                )
            else:
                logger.info(f"Using Service Account credentials file: {GOOGLE_CREDENTIALS_FILE}")
                credentials = service_account.Credentials.from_service_account_file(
                    GOOGLE_CREDENTIALS_FILE,
                    scopes=['https://www.googleapis.com/auth/drive']
                )
            self.service = build('drive', 'v3', credentials=credentials)
            logger.info("Google Drive API initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive API: {e}")
            raise

    def create_folder(self, name, parent_id=None):
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = self.service.files().create(
            body=file_metadata,
            fields='id, webViewLink'
        ).execute()
        logger.info(f"Created folder: {name}")
        return folder

    def find_folder_by_name(self, name, parent_id=None):
        query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = self.service.files().list(q=query, spaces='drive', fields='files(id, name, webViewLink)').execute()
        items = results.get('files', [])
        return items[0] if items else None

    def get_or_create_folder(self, name, parent_id=None):
        folder = self.find_folder_by_name(name, parent_id)
        if folder:
            return folder
        return self.create_folder(name, parent_id)

    def create_client_folder_structure(self, full_name, phone):
        safe_name = self._sanitize_name(full_name)
        folder_name = f"{safe_name} | {phone}"

        # Пошук існуючої папки
        existing = self._find_client_folder_by_phone(phone)
        if existing:
            logger.info(f"Client folder already exists: {existing['name']}")
            client_folder = existing
        else:
            client_folder = self.create_folder(folder_name, ROOT_FOLDER_ID)

        # Створюємо всі підпапки (або знаходимо існуючі)
        credit_folder = self.get_or_create_folder(SUBFOLDERS['credit'], client_folder['id'])
        personal_folder = self.get_or_create_folder(SUBFOLDERS['personal'], client_folder['id'])
        declaration_folder = self.get_or_create_folder(SUBFOLDERS['declaration'], client_folder['id'])
        expenses_folder = self.get_or_create_folder(SUBFOLDERS['expenses_confirmation'], client_folder['id'])
        debt_folder = self.get_or_create_folder(SUBFOLDERS['debt_confirmation'], client_folder['id'])
        additional_folder = self.get_or_create_folder(SUBFOLDERS['additional'], client_folder['id'])

        return {
            'client': client_folder,
            'credit': credit_folder,
            'personal': personal_folder,
            'declaration': declaration_folder,
            'expenses_confirmation': expenses_folder,
            'debt_confirmation': debt_folder,
            'additional': additional_folder
        }

    def _find_client_folder_by_phone(self, phone):
        query = f"name contains '{phone}' and mimeType='application/vnd.google-apps.folder' and '{ROOT_FOLDER_ID}' in parents and trashed=false"
        results = self.service.files().list(q=query, spaces='drive', fields='files(id, name, webViewLink)').execute()
        items = results.get('files', [])
        return items[0] if items else None

    def upload_file(self, file_path, folder_id, original_filename=None):
        filename = original_filename or os.path.basename(file_path)
        file_metadata = {'name': filename, 'parents': [folder_id]}
        media = MediaFileUpload(file_path, resumable=True)
        file = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink, size'
        ).execute()
        logger.info(f"Uploaded file: {filename}")
        return file

    def create_text_file(self, content, filename, folder_id):
        file_metadata = {'name': filename, 'parents': [folder_id], 'mimeType': 'text/plain'}

        # Перевірка існування
        existing = self._find_file_by_name(filename, folder_id)
        media = MediaIoBaseUpload(BytesIO(content.encode('utf-8')), mimetype='text/plain')

        if existing:
            file = self.service.files().update(
                fileId=existing['id'],
                media_body=media,
                fields='id, name, webViewLink, size'
            ).execute()
            logger.info(f"Updated text file: {filename}")
        else:
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink, size'
            ).execute()
            logger.info(f"Created text file: {filename}")

        return file

    def _find_file_by_name(self, name, folder_id):
        query = f"name='{name}' and '{folder_id}' in parents and trashed=false"
        results = self.service.files().list(q=query, spaces='drive', fields='files(id, name, webViewLink)').execute()
        items = results.get('files', [])
        return items[0] if items else None

    @staticmethod
    def _sanitize_name(name):
        forbidden = '<>:"/\\|?*\x00-\x1F'
        for char in forbidden:
            name = name.replace(char, ' ')
        return ' '.join(name.split())

# ============================================================================
# TELEGRAM BOT
# ============================================================================

# Стани
WAITING_NAME, WAITING_PHONE = range(2)

# Стани для анкети декларації
(DECL_START, DECL_QUESTION, DECL_FILES) = range(3)

# Стан для додаткових документів з кастомним ім'ям
ADDITIONAL_DOC_WAITING_NAME = 100

# Callback data
CALLBACK_UPLOAD_PREFIX = "upload_"
CALLBACK_DONE = "done"
CALLBACK_BACK = "back"
CALLBACK_DECL_START = "decl_start"
CALLBACK_DECL_SKIP = "decl_skip"
CALLBACK_DECL_PREVIOUS = "decl_previous"
CALLBACK_DECL_MENU = "decl_menu"

db = Database()
drive = DriveManager()
notification_bot = None

# Словник для зберігання message_id чек-листів клієнтів
# client_telegram_id -> (chat_id, message_id)
client_checklist_messages = {}

async def update_client_checklist(client_id, bot):
    """Оновити чек-лист клієнта (якщо він відкритий)"""
    try:
        client = db.get_client_by_id(client_id)
        if not client or not client.get('telegram_id') or client['telegram_id'] == 0:
            return  # Клієнт не має telegram_id (створений адміном)

        telegram_id = client['telegram_id']
        if telegram_id not in client_checklist_messages:
            return  # Чек-лист не відкритий

        chat_id, message_id = client_checklist_messages[telegram_id]

        # Формуємо оновлений чек-лист
        uploaded_types = db.get_uploaded_types(client['id'])
        has_ecpass = db.get_ec_password(client['id']) is not None
        if has_ecpass and 'ecpass' not in uploaded_types:
            uploaded_types['ecpass'] = 1

        required_count = len(REQUIRED_DOCUMENTS)
        uploaded_required_count = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)

        # Прогрес-бар
        progress_bar = get_progress_bar(uploaded_required_count, required_count)

        message = f"📋 <b>Ваш прогрес: {uploaded_required_count}/{required_count} обов'язкових документів</b>\n\n"
        message += f"{progress_bar}\n\n"
        message += "<b>Обов'язкові документи:</b>\n"

        for doc_key in REQUIRED_DOCUMENTS:
            doc_info = DOCUMENT_TYPES[doc_key]
            emoji = doc_info['emoji']
            name = doc_info.get('short', doc_info['name'])

            if doc_key in uploaded_types:
                count = uploaded_types[doc_key]
                if doc_info.get('multiple'):
                    message += f"✅ {emoji} {name} ({count} файл(ів))\n"
                else:
                    message += f"✅ {emoji} {name}\n"
            else:
                message += f"❌ {emoji} {name}\n"

        optional_docs = [k for k in DOCUMENT_TYPES.keys() if k not in REQUIRED_DOCUMENTS]
        if optional_docs:
            message += f"\n<b>Додаткові документи:</b>\n"
            for doc_key in optional_docs:
                doc_info = DOCUMENT_TYPES[doc_key]
                emoji = doc_info['emoji']
                name = doc_info.get('short', doc_info['name'])

                if doc_key in uploaded_types:
                    count = uploaded_types[doc_key]
                    message += f"✅ {emoji} {name} ({count})\n"
                else:
                    message += f"⚪️ {emoji} {name}\n"

        message += f"\n💡 <i>Натисніть на документ нижче, щоб завантажити</i>"

        # Створюємо кнопки
        buttons = []
        for doc_key, doc_info in DOCUMENT_TYPES.items():
            emoji = doc_info['emoji']
            name = doc_info.get('short', doc_info['name'])
            if doc_key in uploaded_types:
                button_text = f"✅ {name}"
            else:
                button_text = f"{emoji} {name}"
            buttons.append(InlineKeyboardButton(button_text, callback_data=f"{CALLBACK_UPLOAD_PREFIX}{doc_key}"))

        keyboard = []
        for i in range(0, len(buttons), 2):
            row = buttons[i:i+2]
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Оновлюємо повідомлення
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        logger.info(f"Updated checklist for client {client_id} (telegram_id={telegram_id})")

    except Exception as e:
        logger.error(f"Error updating client checklist: {e}")
        # Видаляємо з словника якщо не вдалось оновити (повідомлення вже не існує)
        if telegram_id in client_checklist_messages:
            client_checklist_messages.pop(telegram_id)

def normalize_phone(phone):
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) == 10:
        return f"+380{digits}"
    elif len(digits) == 12 and digits.startswith('380'):
        return f"+{digits}"
    elif digits.startswith('0'):
        return f"+38{digits}"
    elif digits:
        return f"+{digits}"
    return ''

def get_active_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отримати активного клієнта: через admin_mode або звичайного юзера"""
    # Якщо адмін увійшов як клієнт
    if 'admin_mode' in context.user_data:
        client_id = context.user_data['admin_mode']['client_id']
        client = db.get_client_by_id(client_id)
        admin_id = context.user_data['admin_mode']['admin_telegram_id']
        return client, admin_id  # (client, admin_id)

    # Звичайний клієнт
    user_id = update.effective_user.id
    client = db.get_client_by_telegram_id(user_id)
    return client, None  # (client, None)

async def notify_admins(message, parse_mode='HTML'):
    """Отправить уведомление всем админам из файла"""
    if not notification_bot:
        return

    admin_ids = load_admins()
    if not admin_ids:
        logger.warning("No admins to notify")
        return

    for admin_id in admin_ids:
        try:
            await notification_bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
            logger.info(f"Notification sent to admin: {admin_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

async def check_and_send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Перевірка неактивних клієнтів та відправка нагадувань

    Логіка частоти:
    - 3 дні неактивності → перше нагадування
    - 6 днів → друге нагадування (через 3 дні після першого)
    - 9 днів → третє нагадування (через 3 дні після другого)
    - 10+ днів → щоденні нагадування
    """
    try:
        logger.info("Starting reminder check...")

        # Отримуємо всіх неактивних клієнтів (більше 3 днів)
        inactive_clients = db.get_inactive_clients()

        if not inactive_clients:
            logger.info("No inactive clients found")
            return

        logger.info(f"Found {len(inactive_clients)} inactive clients")

        for client in inactive_clients:
            try:
                # Підраховуємо кількість днів неактивності
                now_utc = datetime.now(timezone.utc)

                # Переконуємося що last_activity має timezone
                last_activity = client['last_activity']
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=timezone.utc)

                days_inactive = (now_utc - last_activity).days

                # Отримуємо останнє нагадування
                last_reminder = db.get_last_reminder(client['id'])

                # Визначаємо чи потрібно надіслати нагадування
                should_send = False

                if not last_reminder:
                    # Перше нагадування - якщо 3+ дні неактивності
                    should_send = days_inactive >= 3
                else:
                    # Підраховуємо час з останнього нагадування
                    sent_at = last_reminder['sent_at']
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)

                    days_since_last = (now_utc - sent_at).days

                    if days_inactive < 10:
                        # До 10 днів - кожні 3 дні
                        should_send = days_since_last >= 3
                    else:
                        # 10+ днів - щоденно
                        should_send = days_since_last >= 1

                if should_send:
                    # Формуємо повідомлення залежно від прогресу
                    uploaded_types = db.get_uploaded_types(client['id'])
                    # Перевіряємо пароль ЕЦП
                    has_ecpass = db.get_ec_password(client['id']) is not None
                    if has_ecpass:
                        uploaded_types['ecpass'] = 1
                    # Рахуємо тільки обов'язкові документи
                    required_uploaded = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)
                    required_total = len(REQUIRED_DOCUMENTS)

                    if required_uploaded == 0:
                        message = (
                            f"👋 Вітаю, {client['full_name']}!\n\n"
                            f"😊 Нагадуємо, що ви ще не завантажили жодного документа.\n\n"
                            f"📋 Будь ласка, почніть завантаження документів, щоб прискорити процес обробки.\n\n"
                            f"💡 Натисніть /start щоб побачити чек-лист документів."
                        )
                    else:
                        message = (
                            f"👋 Вітаю, {client['full_name']}!\n\n"
                            f"📊 Ви завантажили {required_uploaded} з {required_total} обов'язкових документів.\n\n"
                            f"😊 Будь ласка, завершіть завантаження решти документів.\n\n"
                            f"🎁 Нагадуємо: при зборі всіх документів ви отримаєте бонус від компанії!\n\n"
                            f"💡 Натисніть /start щоб продовжити."
                        )

                    # Відправляємо нагадування клієнту
                    if client['telegram_id']:
                        await context.bot.send_message(
                            chat_id=client['telegram_id'],
                            text=message,
                            parse_mode='HTML'
                        )

                        # Логуємо відправлене нагадування
                        db.log_reminder(client['id'], days_inactive)
                        db.log_notification(
                            client_id=client['id'],
                            notification_type='reminder_sent',
                            message=f"Нагадування надіслано ({days_inactive} днів неактивності)"
                        )

                        logger.info(f"Reminder sent to {client['full_name']} ({days_inactive} days inactive)")

                        # Повідомляємо адмінів
                        await notify_admins(
                            f"🔔 Надіслано нагадування клієнту\n\n"
                            f"👤 {client['full_name']}\n"
                            f"📱 {client['phone']}\n"
                            f"📊 Неактивний: {days_inactive} днів\n"
                            f"📄 Завантажено: {required_uploaded}/{required_total} документів"
                        )

            except Exception as e:
                logger.error(f"Error sending reminder to client {client['id']}: {e}")
                continue

        logger.info("Reminder check completed")

    except Exception as e:
        logger.error(f"Error in check_and_send_reminders: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"User {user.id} started bot")

    # Проверка Deep Link для админов
    if context.args and len(context.args) > 0:
        code = context.args[0]
        if code.startswith('admin_') and code.split('_', 1)[1] == ADMIN_SECRET_CODE:
            # Сохраняем админа в файл для уведомлений
            save_admin(user.id)

            # Показываем приветствие админу
            name = user.full_name or f"Admin {user.id}"
            await update.message.reply_text(
                f"✅ Привет, {name}!\n\n"
                f"Это админ-панель бота для сбора документов.\n\n"
                f"📬 <b>Уведомления будут приходить в этот чат:</b>\n"
                f"• Новые клиенты (регистрация)\n"
                f"• Загруженные документы\n"
                f"• Завершение сбора документов\n\n"
                f"🔍 <b>Доступные команды:</b>\n"
                f"/info +380XXXXXXXXX - проверить какие документы загрузил клиент\n\n"
                f"📌 <b>Пример использования:</b>\n"
                f"/info +380501234567",
                parse_mode='HTML'
            )
            logger.info(f"Admin panel accessed: {name} ({user.id})")
            return ConversationHandler.END

    # Звичайна реєстрація клієнта
    client = db.get_client_by_telegram_id(user.id)
    if client:
        await update.message.reply_text(
            f"Вітаю знову, {client['full_name']}! 👋\n\n"
            f"Ви вже зареєстровані в системі.\n"
            f"📊 Ваш прогрес збору документів можна переглянути, натиснувши кнопку \"📋 Чек-лист\" нижче.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    # Відправляємо відео-інструкцію
    try:
        await update.message.reply_video(
            video="BAACAgIAAxkBAAII52k6wLwc0RjDncog2l1OHxU4n40wAAKhjwACJPTYSWOdyLqLb7UTNgQ",
            caption="📹 Інструкція: Як користуватися ботом для збору документів",
            supports_streaming=True
        )
    except Exception as e:
        logger.error(f"Failed to send video: {e}")

    await update.message.reply_text(
        "👆 <b>Перегляньте відео вище - це коротка інструкція про те, як працювати з ботом!</b>\n\n"
        "Вітаю! 👋\n\n"
        "Я допоможу вам зібрати всі необхідні документи для списання боргів.\n\n"
        "📹 <b>У відео показано:</b>\n"
        "• Як реєструватися в боті\n"
        "• Як завантажувати документи\n"
        "• Які документи потрібні\n\n"
        "🎁 <b>БОНУС:</b> При завершенні збору всіх документів ви отримаєте "
        "подарунок від нашої компанії!\n\n"
        "⚠️ <b>ВАЖЛИВО:</b> Обов'язково перегляньте відео вище перед початком роботи!\n\n"
        "Почнемо? Будь ласка, введіть ваше <b>ПІБ</b> (Прізвище Ім'я По батькові):",
        parse_mode='HTML'
    )
    return WAITING_NAME

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    if len(full_name) < 5:
        await update.message.reply_text("⚠️ ПІБ занадто коротке. Будь ласка, введіть повне ПІБ:")
        return WAITING_NAME

    context.user_data['full_name'] = full_name
    await update.message.reply_text(
        f"Дякую, {full_name}! 😊\n\n"
        f"Тепер введіть ваш <b>номер телефону</b> у форматі:\n"
        f"+380XXXXXXXXX\n\n"
        f"Або натисніть кнопку нижче, щоб поділитися номером 📱",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Поділитися номером", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    return WAITING_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    phone = normalize_phone(phone)
    if not phone or len(phone) < 10:
        await update.message.reply_text(
            "⚠️ Невірний формат номера. Спробуйте ще раз:\n"
            "Приклад: +380501234567"
        )
        return WAITING_PHONE

    full_name = context.user_data['full_name']
    client = db.create_client(update.effective_user.id, full_name, phone)

    try:
        folders = drive.create_client_folder_structure(full_name, phone)
        db.update_client_drive_folder(client['id'], folders['client']['id'], folders['client']['webViewLink'])
        context.user_data['folders'] = folders

        # Логируем регистрацию клиента
        db.log_notification(
            client_id=client['id'],
            notification_type='client_registered',
            message=f"Клієнт зареєстрований: {full_name}, {phone}"
        )
    except Exception as e:
        logger.error(f"Failed to create Drive folders: {e}")
        await update.message.reply_text(
            "❌ Виникла помилка при створенні папки. Спробуйте пізніше або зв'яжіться з менеджером."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Реєстрація завершена!\n\n"
        f"👤 ПІБ: {full_name}\n"
        f"📱 Телефон: {phone}\n\n"
        f"📂 Для вас створено особисту папку на Google Drive.\n\n"
        f"📋 <b>Що далі?</b>\n"
        f"1. Натисніть кнопку \"📋 Чек-лист\" щоб побачити список документів\n"
        f"2. Виберіть документ, який хочете завантажити\n"
        f"3. Надішліть файл(и)\n"
        f"4. Натисніть \"✅ Готово\" після завантаження\n\n"
        f"🎁 Не забувайте: при зборі всіх документів ви отримаєте бонус від компанії!\n\n"
        f"Успіхів! 💪",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

    await notify_admins(
        f"🆕 Новий клієнт зареєстрований!\n\n"
        f"👤 {full_name}\n"
        f"📱 {phone}\n"
        f"🆔 Telegram: {update.effective_user.id}\n"
        f"📊 Статус: in_progress (0/9 документів)\n"
        f"📁 <a href=\"{folders['client']['webViewLink']}\">Відкрити папку на Drive</a>"
    )

    return ConversationHandler.END

async def show_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message=False):
    query = update.callback_query

    # Використовуємо get_active_client для підтримки адмін-режиму
    client, admin_id = get_active_client(update, context)

    if not client:
        message = "❌ Ви ще не зареєстровані. Натисніть /start"
        if query:
            await query.answer(message)
        else:
            await update.message.reply_text(message)
        return

    uploaded_types = db.get_uploaded_types(client['id'])
    has_ecpass = db.get_ec_password(client['id']) is not None
    if has_ecpass and 'ecpass' not in uploaded_types:
        uploaded_types['ecpass'] = 1

    required_count = len(REQUIRED_DOCUMENTS)
    uploaded_required_count = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)

    # Прогрес-бар
    progress_bar = get_progress_bar(uploaded_required_count, required_count)

    message = f"📋 <b>Ваш прогрес: {uploaded_required_count}/{required_count} обов'язкових документів</b>\n\n"
    message += f"{progress_bar}\n\n"
    message += "<b>Обов'язкові документи:</b>\n"

    for doc_key in REQUIRED_DOCUMENTS:
        doc_info = DOCUMENT_TYPES[doc_key]
        emoji = doc_info['emoji']
        name = doc_info.get('short', doc_info['name'])

        if doc_key in uploaded_types:
            count = uploaded_types[doc_key]
            if doc_info.get('multiple'):
                message += f"✅ {emoji} {name} ({count} файл(ів))\n"
            else:
                message += f"✅ {emoji} {name}\n"
        else:
            message += f"❌ {emoji} {name}\n"

    optional_docs = [k for k in DOCUMENT_TYPES.keys() if k not in REQUIRED_DOCUMENTS]
    if optional_docs:
        message += f"\n<b>Додаткові документи:</b>\n"
        for doc_key in optional_docs:
            doc_info = DOCUMENT_TYPES[doc_key]
            emoji = doc_info['emoji']
            name = doc_info.get('short', doc_info['name'])

            if doc_key in uploaded_types:
                count = uploaded_types[doc_key]
                message += f"✅ {emoji} {name} ({count})\n"
            else:
                message += f"⚪️ {emoji} {name}\n"

    # Додаємо статус анкети декларації
    declaration = db.get_declaration(client['id'])
    declaration_completed = declaration and declaration['status'] == 'completed'

    message += f"\n<b>Анкета:</b>\n"
    if declaration_completed:
        message += f"✅ 📋 Анкета декларації\n"
    else:
        message += f"❌ 📋 Анкета декларації\n"

    message += f"\n💡 <i>Натисніть на документ нижче, щоб завантажити</i>"

    # Создаём кнопки и группируем их по 2 в строке
    # Исключаем 'additional_docs' из основного списка
    buttons = []
    for doc_key, doc_info in DOCUMENT_TYPES.items():
        if doc_key == 'additional_docs':
            continue  # Пропускаем, добавим отдельно в конце
        emoji = doc_info['emoji']
        name = doc_info.get('short', doc_info['name'])
        # Меняем emoji с обычного на ✅ после загрузки
        if doc_key in uploaded_types:
            button_text = f"✅ {name}"
        else:
            button_text = f"{emoji} {name}"
        buttons.append(InlineKeyboardButton(button_text, callback_data=f"{CALLBACK_UPLOAD_PREFIX}{doc_key}"))

    # Группируем кнопки по 2 в строке
    keyboard = []
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard.append(row)

    # Додаємо останній ряд: "Анкета декларації" (зліва) + "Додаткові документи" (справа)
    if declaration_completed:
        decl_button_text = "✅ Анкета декларації"
    else:
        decl_button_text = "📋 Анкета декларації"

    # Кнопка "Додаткові документи"
    if 'additional_docs' in uploaded_types:
        additional_button_text = "✅ Додаткові документи"
    else:
        additional_button_text = "📎 Додаткові документи"

    last_row = [
        InlineKeyboardButton(decl_button_text, callback_data=CALLBACK_DECL_START),
        InlineKeyboardButton(additional_button_text, callback_data=f"{CALLBACK_UPLOAD_PREFIX}additional_docs")
    ]
    keyboard.append(last_row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query and not force_new_message:
        await query.answer()
        sent_msg = await query.edit_message_text(message, parse_mode='HTML', reply_markup=reply_markup)
        # Зберігаємо message_id для оновлення (тільки для реальних клієнтів, не адмінів)
        if not admin_id and client.get('telegram_id'):
            client_checklist_messages[client['telegram_id']] = (update.effective_chat.id, query.message.message_id)
    else:
        # Отправляем новое сообщение (либо нет query, либо force_new_message=True)
        if query:
            await query.answer()
        sent_msg = await update.effective_chat.send_message(message, parse_mode='HTML', reply_markup=reply_markup)
        # Зберігаємо message_id для оновлення (тільки для реальних клієнтів, не адмінів)
        if not admin_id and client.get('telegram_id'):
            client_checklist_messages[client['telegram_id']] = (update.effective_chat.id, sent_msg.message_id)

async def handle_upload_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    doc_key = query.data.replace(CALLBACK_UPLOAD_PREFIX, '')
    doc_info = DOCUMENT_TYPES.get(doc_key)

    if not doc_info:
        await query.edit_message_text("❌ Невідомий тип документа")
        return

    context.user_data['uploading_doc_type'] = doc_key
    context.user_data['uploaded_files'] = []

    if doc_info.get('is_text'):
        await query.edit_message_text(
            f"🔐 <b>{doc_info['name']}</b>\n\n"
            f"Будь ласка, надішліть пароль від ЕЦП у вигляді текстового повідомлення.\n\n"
            f"💡 Просто напишіть пароль, і бот автоматично його розпізнає та збереже.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)
            ]])
        )
    else:
        # Используем description если есть, иначе name
        doc_title = doc_info.get('description', doc_info['name'])
        message = f"{doc_info['emoji']} <b>{doc_title}</b>\n\n"
        if doc_info.get('multiple'):
            message += f"📎 Надішліть файл(и) документів.\n"
        else:
            message += f"📎 Надішліть файл документа.\n"

        # Додаємо підказку про можливість множинної загрузки (для всіх крім additional_docs, ecp, ecpass)
        if doc_key not in ['additional_docs', 'ecp', 'ecpass']:
            message += f"\n💡 <i>При необхідності, Ви можете завантажити одразу кілька документів.\nПросто надішліть їх одним повідомленням, а потім натисніть \"Готово\".</i>\n"

        # Добавляем видео-ссылку если есть
        if doc_info.get('video'):
            message += f"\n📺 <a href=\"{doc_info['video']}\">Відео-інструкція: як отримати цей документ</a>"

        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)
            ]]),
            disable_web_page_preview=True
        )

        # Зберігаємо message_id для подальшого видалення
        context.user_data['upload_instruction_message_id'] = query.message.message_id

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений (только для пароля ЕЦП)"""
    # Використовуємо get_active_client для підтримки адмін-режиму
    client, admin_id = get_active_client(update, context)

    if not client:
        await update.message.reply_text("❌ Ви ще не зареєстровані. Натисніть /start")
        return

    if 'uploading_doc_type' not in context.user_data:
        await update.message.reply_text(
            "⚠️ Спочатку виберіть тип документа через кнопку \"📋 Чек-лист\"",
            reply_markup=get_main_keyboard()
        )
        return

    doc_key = context.user_data['uploading_doc_type']
    doc_info = DOCUMENT_TYPES.get(doc_key)

    # Пароль ЕЦП - автоматическое сохранение
    if doc_info.get('is_text'):
        password = update.message.text.strip()

        try:
            # Сохраняем пароль в БД
            logger.info(f"Saving ECP password for client_id={client['id']}")
            password_id = db.save_ec_password(client['id'], password)
            logger.info(f"ECP password saved to DB: password_id={password_id}, client_id={client['id']}, password={password}")

            # Сохраняем на Drive
            folders = drive.create_client_folder_structure(client['full_name'], client['phone'])
            personal_folder_id = folders['personal']['id']
            drive.create_text_file(password, 'Пароль_ЕЦП.txt', personal_folder_id)
            logger.info(f"ECP password file created on Drive for client_id={client['id']}")

            db.update_last_activity(client['id'])

            # Логируем в notifications_log
            db.log_notification(
                client_id=client['id'],
                notification_type='ecp_password_saved',
                message=f"Пароль від ЕЦП збережено: {password}"
            )

            # Очищаем состояние
            context.user_data.pop('uploading_doc_type', None)
            context.user_data.pop('uploaded_files', None)
            context.user_data.pop('ec_password', None)
            context.user_data.pop('upload_status_message', None)

            await update.message.reply_text("✅ Пароль від ЕЦП збережено!")

            # Уведомляем админов
            await notify_admins(
                f"🔐 Клієнт зберіг пароль від ЕЦП\n\n"
                f"👤 {client['full_name']}\n"
                f"📱 {client['phone']}\n"
                f"🔑 Пароль: {password}\n"
                f"📊 Статус: {client['status']}\n"
                f"📁 <a href=\"{client['drive_folder_url']}\">Відкрити папку на Drive</a>"
            )

            # Показываем чеклист новым сообщением
            import asyncio
            await asyncio.sleep(0.5)
            await show_checklist(update, context, force_new_message=True)

        except Exception as e:
            logger.error(f"Error saving ECP password: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Помилка збереження пароля: {str(e)}")

        return
    else:
        # Если выбран не текстовый тип документа, а пользователь отправил текст
        await update.message.reply_text(
            "⚠️ Будь ласка, надішліть файл (не текстове повідомлення).\\n"
            "Або натисніть \\\"✅ Готово\\\" для завершення."
        )

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client, admin_id = get_active_client(update, context)

    if not client:
        await update.message.reply_text("❌ Ви ще не зареєстровані. Натисніть /start")
        return

    if 'uploading_doc_type' not in context.user_data:
        await update.message.reply_text(
            "⚠️ Спочатку виберіть тип документа через кнопку \"📋 Чек-лист\"",
            reply_markup=get_main_keyboard()
        )
        return

    doc_key = context.user_data['uploading_doc_type']
    doc_info = DOCUMENT_TYPES.get(doc_key)

    if not update.message.document and not update.message.photo:
        await update.message.reply_text(
            "⚠️ Будь ласка, надішліть файл (не текстове повідомлення).\n"
            "Або натисніть \"✅ Готово\" для завершення."
        )
        return

    if update.message.document:
        file = update.message.document
        original_file_name = file.file_name
    else:
        file = update.message.photo[-1]
        original_file_name = f"photo_{file.file_id}.jpg"

    # ============================================================================
    # СПЕЦІАЛЬНА ЛОГІКА ДЛЯ ДОДАТКОВИХ ДОКУМЕНТІВ
    # ============================================================================
    if doc_info.get('requires_custom_name', False):
        # Завантажуємо файл тимчасово
        tg_file = await context.bot.get_file(file.file_id)
        temp_path = os.path.join(tempfile.gettempdir(), original_file_name)
        await tg_file.download_to_drive(temp_path)

        # Зберігаємо інформацію про файл у context
        file_ext = os.path.splitext(original_file_name)[1]
        context.user_data['additional_doc_temp_path'] = temp_path
        context.user_data['additional_doc_ext'] = file_ext
        context.user_data['additional_doc_file_id'] = file.file_id

        # Запитуємо назву документа
        await update.message.reply_text(
            "📝 Введіть назву для цього документа:\n\n"
            "Наприклад: Довідка з роботи, Договір оренди, тощо"
        )

        return ADDITIONAL_DOC_WAITING_NAME

    # Видаляємо попереднє повідомлення з інструкцією (перший раз)
    if 'upload_instruction_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['upload_instruction_message_id']
            )
            context.user_data.pop('upload_instruction_message_id')
        except:
            pass

    # Видаляємо попереднє повідомлення зі статусом
    if 'upload_status_message' in context.user_data:
        try:
            await context.user_data['upload_status_message'].delete()
        except:
            pass

    # Показываем сообщение о загрузке
    loading_msg = await update.message.reply_text("⏳ Обробляю та перевіряю файл...")

    try:
        # Получаем расширение файла
        file_ext = os.path.splitext(original_file_name)[1]

        # Создаём новое имя файла: ТипДокумента_Имя_Фамилия.расширение
        doc_type_name = doc_info.get('short', doc_info['name']).replace('/', '_').replace('\\', '_')
        client_name_parts = client['full_name'].split()
        if len(client_name_parts) >= 2:
            # Имя Фамилия (первые 2 слова)
            short_name = f"{client_name_parts[0]}_{client_name_parts[1]}"
        else:
            short_name = client['full_name'].replace(' ', '_')

        # Имя файла без нумерации
        new_file_name = f"{doc_type_name}_{short_name}{file_ext}"

        tg_file = await context.bot.get_file(file.file_id)
        temp_path = os.path.join(tempfile.gettempdir(), original_file_name)
        await tg_file.download_to_drive(temp_path)

        # ============================================================================
        # AI-ПЕРЕВІРКА ДОКУМЕНТА (пропускаємо для додаткових документів)
        # ============================================================================
        if doc_info.get('skip_ai_validation', False):
            validation_result = None
        else:
            validation_result = ai_validator.validate_document(temp_path, doc_key)

        # Якщо документ REJECTED - НЕ завантажуємо на Drive
        if validation_result and validation_result.is_rejected():
            # Видаляємо тимчасовий файл
            os.remove(temp_path)

            # Видаляємо повідомлення про завантаження
            await loading_msg.delete()

            # Повідомляємо клієнта про відхилення з кнопками
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Спробувати ще раз", callback_data=f"upload_{doc_key}")],
                [InlineKeyboardButton("« Назад до чек-листа", callback_data=CALLBACK_BACK)]
            ])

            await update.message.reply_text(
                validation_result.get_user_message(),
                parse_mode='HTML',
                reply_markup=keyboard
            )

            # Логуємо відхилення
            db.log_notification(
                client_id=client['id'],
                notification_type='document_rejected',
                message=f"AI відхилив документ: {doc_info['name']} - причина: {validation_result.error_code}",
                admin_telegram_id=admin_id
            )

            logger.info(f"Document REJECTED by AI: {doc_key} for client {client['id']} - reason: {validation_result.error_code}")
            return  # Припиняємо виконання, документ НЕ завантажено

        # ============================================================================
        # ЗАВАНТАЖЕННЯ НА DRIVE (для ACCEPTED та UNCERTAIN)
        # ============================================================================
        folder_type = doc_info['folder']
        folders = drive.create_client_folder_structure(client['full_name'], client['phone'])
        target_folder_id = folders[folder_type]['id']

        # Загружаем с новым именем
        drive_file = drive.upload_file(temp_path, target_folder_id, new_file_name)

        # Додаємо документ в БД
        document_id = db.add_document(
            client_id=client['id'],
            document_type=doc_key,
            file_name=new_file_name,
            drive_file_id=drive_file['id'],
            drive_file_url=drive_file['webViewLink'],
            file_size=int(drive_file.get('size', 0)),
            uploaded_by_admin_id=admin_id
        )

        # Зберігаємо результат AI-валідації (якщо є)
        if validation_result:
            try:
                db.save_document_validation(
                    document_id=document_id,
                    validation_status=validation_result.status,
                    ai_response=validation_result.ai_response
                )
                db.update_document_validation_status(document_id, validation_result.status)
            except Exception as e:
                # Логуємо помилку БД, але не показуємо користувачу
                logger.error(f"Error saving AI validation to DB: {e}", exc_info=True)

        # Логируем в notifications_log
        notification_type = 'document_uploaded'
        if validation_result:
            if validation_result.is_accepted():
                notification_type = 'document_uploaded_accepted'
            elif validation_result.is_uncertain():
                notification_type = 'document_uploaded_uncertain'

        db.log_notification(
            client_id=client['id'],
            notification_type=notification_type,
            message=f"{'Адмін завантажив' if admin_id else 'Завантажено'} документ: {doc_info['name']} - {new_file_name} (AI: {validation_result.status if validation_result else 'skipped'})",
            admin_telegram_id=admin_id
        )

        # Сповіщаємо адмінів про завантаження (ТІЛЬКИ для клієнтів, не адмінів)
        # Об'єднана нотифікація з прогресом і статусом AI
        if not admin_id:
            # Рахуємо прогрес
            uploaded_types = db.get_uploaded_types(client['id'])
            has_ecpass = db.get_ec_password(client['id']) is not None
            if has_ecpass:
                uploaded_types['ecpass'] = 1
            required_uploaded = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)
            required_total = len(REQUIRED_DOCUMENTS)

            # Визначаємо статус AI для повідомлення
            ai_status_emoji = "✅"
            ai_status_text = validation_result.status if validation_result else 'не перевірено'
            notification_title = "📄 <b>Клієнт завантажив документ</b>"

            if validation_result:
                if validation_result.is_accepted():
                    ai_status_emoji = "✅"
                elif validation_result.is_uncertain():
                    ai_status_emoji = "⚠️"
                    notification_title = "⚠️ <b>Документ потребує перевірки</b>"

            await notify_admins(
                f"{notification_title}\n\n"
                f"👤 {client['full_name']}\n"
                f"📱 {client['phone']}\n"
                f"📑 {doc_info['name']}\n"
                f"{ai_status_emoji} <b>AI:</b> {ai_status_text}\n"
                f"📊 <b>Прогрес:</b> {required_uploaded}/{required_total} документів\n\n"
                f"📁 <a href=\"{drive_file['webViewLink']}\">Переглянути документ</a>\n"
                f"📂 <a href=\"{client['drive_folder_url']}\">Папка клієнта</a>"
            )

        db.update_last_activity(client['id'])
        os.remove(temp_path)

        # Оновлюємо чек-лист клієнта (якщо адмін завантажує за клієнта)
        if admin_id:
            await update_client_checklist(client['id'], context.bot)

        if 'uploaded_files' not in context.user_data:
            context.user_data['uploaded_files'] = []
        context.user_data['uploaded_files'].append({'name': new_file_name, 'status': '✅'})

        # Удаляем сообщение о загрузке
        await loading_msg.delete()

        # Формируем сообщение со списком всех загруженных файлов
        uploaded_files = context.user_data['uploaded_files']
        count = len(uploaded_files)

        # Повідомлення для користувача
        if validation_result:
            user_message = validation_result.get_user_message()
        else:
            user_message = "✅ Документ успішно завантажено!"

        message = f"{user_message}\n\n"
        message += f"<b>Завантажено файлів: {count}</b>\n\n"
        for idx, file_info in enumerate(uploaded_files, 1):
            message += f"{idx}. {file_info['name']} — {file_info['status']}\n"

        message += f"\n💡 Надішліть ще файли або натисніть \"Готово\""

        # Создаём новое сообщение с кнопкой "Готово" внизу
        msg = await update.message.reply_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Готово", callback_data=CALLBACK_DONE),
                InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)
            ]])
        )
        context.user_data['upload_status_message'] = msg

    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        # Удаляем сообщение о загрузке в случае ошибки
        if loading_msg:
            await loading_msg.delete()
        await update.message.reply_text(
            f"❌ Помилка завантаження файлу: {str(e)}\n"
            f"Спробуйте ще раз або зв'яжіться з менеджером."
        )

async def handle_additional_doc_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення назви для додаткового документа"""
    client, admin_id = get_active_client(update, context)

    if not client:
        await update.message.reply_text("❌ Ви ще не зареєстровані. Натисніть /start")
        return

    custom_name = update.message.text.strip()

    if not custom_name:
        await update.message.reply_text("⚠️ Назва не може бути порожньою. Введіть назву документа:")
        return ADDITIONAL_DOC_WAITING_NAME

    # Отримуємо збережені дані
    temp_path = context.user_data.get('additional_doc_temp_path')
    file_ext = context.user_data.get('additional_doc_ext')

    if not temp_path or not file_ext:
        await update.message.reply_text("❌ Помилка: файл не знайдено. Спробуйте завантажити заново.")
        context.user_data.pop('uploading_doc_type', None)
        await show_checklist(update, context, force_new_message=True)
        return

    try:
        loading_msg = await update.message.reply_text("⏳ Завантажую документ на Drive...")

        # Створюємо ім'я файлу з кастомною назвою
        safe_name = custom_name.replace('/', '_').replace('\\', '_')
        new_file_name = f"{safe_name}{file_ext}"

        # Отримуємо папку для завантаження
        doc_key = context.user_data.get('uploading_doc_type')
        doc_info = DOCUMENT_TYPES.get(doc_key)
        folder_type = doc_info['folder']
        folders = drive.create_client_folder_structure(client['full_name'], client['phone'])
        target_folder_id = folders[folder_type]['id']

        # Завантажуємо файл на Drive
        drive_file = drive.upload_file(temp_path, target_folder_id, new_file_name)

        # Додаємо документ в БД
        document_id = db.add_document(
            client_id=client['id'],
            document_type=doc_key,
            file_name=new_file_name,
            drive_file_id=drive_file['id'],
            drive_file_url=drive_file['webViewLink'],
            file_size=int(drive_file.get('size', 0)),
            uploaded_by_admin_id=admin_id
        )

        # Видаляємо тимчасовий файл
        os.remove(temp_path)

        # Очищуємо тимчасові дані
        context.user_data.pop('additional_doc_temp_path', None)
        context.user_data.pop('additional_doc_ext', None)
        context.user_data.pop('additional_doc_file_id', None)

        await loading_msg.delete()

        # Логуємо
        db.log_notification(
            client_id=client['id'],
            notification_type='document_uploaded',
            message=f"Завантажено додатковий документ: {new_file_name}",
            admin_telegram_id=admin_id
        )

        # Уведомляємо адмінів
        await notify_admins(
            f"📎 Додатковий документ завантажено\n\n"
            f"👤 {client['full_name']}\n"
            f"📱 {client['phone']}\n"
            f"📄 {new_file_name}\n"
            f"📁 <a href=\"{drive_file['webViewLink']}\">Переглянути файл</a>"
        )

        # Питаємо чи хоче завантажити ще
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data=CALLBACK_DONE)],
            [InlineKeyboardButton("📎 Завантажити ще один", callback_data=f"upload_additional_docs")]
        ])

        await update.message.reply_text(
            f"✅ Документ \"{custom_name}\" успішно завантажено!\n\n"
            f"Бажаєте завантажити ще один додатковий документ?",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Error uploading additional document: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Помилка завантаження: {str(e)}")
        # Очищуємо дані
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        context.user_data.pop('additional_doc_temp_path', None)
        context.user_data.pop('additional_doc_ext', None)
        context.user_data.pop('additional_doc_file_id', None)
        context.user_data.pop('uploading_doc_type', None)
        await show_checklist(update, context, force_new_message=True)

async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Використовуємо get_active_client для підтримки адмін-режиму
    client, admin_id = get_active_client(update, context)

    if 'uploading_doc_type' not in context.user_data:
        await query.edit_message_text("⚠️ Немає активного завантаження")
        return

    doc_key = context.user_data['uploading_doc_type']
    doc_info = DOCUMENT_TYPES[doc_key]
    uploaded_count = len(context.user_data.get('uploaded_files', []))

    # Для additional_docs просто показуємо чек-лист без повідомлення (файли завантажуються окремо)
    if doc_key == 'additional_docs':
        context.user_data.pop('uploading_doc_type', None)
        context.user_data.pop('uploaded_files', None)
        await query.delete_message()
        await show_checklist(update, context, force_new_message=True)
        return

    # Для обычных файлов (не пароль ЕЦП)
    if uploaded_count == 0:
        message = f"⚠️ Ви не завантажили жодного файлу для \"{doc_info['name']}\""
    else:
        # Показываем список загруженных файлов
        uploaded_files = context.user_data.get('uploaded_files', [])
        message = f"🎉 <b>Документ додано!</b>\n\n"
        message += f"✅ Завантажено файлів: {uploaded_count}\n\n"
        message += "📎 <b>Список файлів:</b>\n"
        for idx, file_info in enumerate(uploaded_files, 1):
            file_name = file_info['name'] if isinstance(file_info, dict) else file_info
            message += f"{idx}. {file_name}\n"

    context.user_data.pop('uploading_doc_type', None)
    context.user_data.pop('uploaded_files', None)
    context.user_data.pop('ec_password', None)
    context.user_data.pop('upload_status_message', None)

    uploaded_types = db.get_uploaded_types(client['id'])
    has_ecpass = db.get_ec_password(client['id']) is not None
    if has_ecpass:
        uploaded_types['ecpass'] = 1

    required_uploaded = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)
    required_total = len(REQUIRED_DOCUMENTS)

    # Додаємо прогрес-бар
    progress_bar = get_progress_bar(required_uploaded, required_total)
    message += f"\n\n📊 <b>Ваш прогрес: {required_uploaded}/{required_total} обов'язкових документів</b>\n\n"
    message += f"{progress_bar}"

    # Перевіряємо старий статус ДО оновлення
    old_status = client['status']

    if required_uploaded == required_total:
        # Оновлюємо статус
        db.update_client_status(client['id'], 'completed')

        # Перевіряємо чи це перший раз
        if old_status != 'completed':
            # 🎉 ПЕРШИЙ РАЗ - повне привітання
            db.log_notification(
                client_id=client['id'],
                notification_type='collection_completed',
                message=f"Клієнт завершив збір всіх обов'язкових документів ({required_total}/{required_total})"
            )

            message += (
                "\n\n🎉 <b>ВІТАЄМО! ВИ ЗІБРАЛИ ВСІ ДОКУМЕНТИ!</b>\n\n"
                "✅ Всі обов'язкові документи успішно завантажено!\n\n"
                "🎁 <b>Ви отримали бонус від компанії!</b>\n"
                "Зв'яжіться з менеджером для отримання подарунка.\n\n"
                "💪 Дякуємо за вашу наполегливість!"
            )

            await notify_admins(
                f"🎉 Клієнт завершив збір ОБОВ'ЯЗКОВИХ документів!\n\n"
                f"👤 {client['full_name']}\n"
                f"📱 {client['phone']}\n"
                f"📊 Статус: completed ({required_total}/{required_total} документів)\n"
                f"📁 <a href=\"{client['drive_folder_url']}\">Відкрити папку на Drive</a>"
            )
        else:
            # Вже був completed - додавання після завершення
            message += "\n\n✅ Документ додано! Всі обов'язкові документи зібрані."

            await notify_admins(
                f"📎 Клієнт завантажив додатковий документ\n\n"
                f"👤 {client['full_name']}\n"
                f"📱 {client['phone']}\n"
                f"📑 {doc_info['name']}\n"
                f"📊 Статус: completed (9/9 + додаткові)\n"
                f"📁 <a href=\"{client['drive_folder_url']}\">Відкрити папку на Drive</a>"
            )
    else:
        # Ще не всі документи
        # Мотивуюче повідомлення залежно від прогресу
        remaining = required_total - required_uploaded
        if remaining == 1:
            message += "\n\n🔥 <b>Залишився всього 1 документ!</b> Ви майже у фінішній прямій! 🚀"
        elif remaining == 2:
            message += "\n\n💪 <b>Залишилось 2 документи!</b> Продовжуйте, ви чудово справляєтесь! ⭐"
        elif remaining <= 4:
            message += f"\n\n✨ <b>Залишилось {remaining} документи!</b> Ще трохи і все готово! 🎯"
        else:
            message += f"\n\n🚀 <b>Чудова робота!</b> Продовжуйте у тому ж дусі! 💪"

        # Нотифікація вже відправлена в handle_file_upload(), не дублюємо

    # Отправляем итоговое сообщение
    await query.edit_message_text(message, parse_mode='HTML')

    # Показываем чеклист новым сообщением
    import asyncio
    await asyncio.sleep(0.5)
    await show_checklist(update, context, force_new_message=True)

async def handle_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('uploading_doc_type', None)
    context.user_data.pop('uploaded_files', None)
    context.user_data.pop('ec_password', None)
    await show_checklist(update, context)

def get_progress_bar(current, total, length=20):
    """Створити візуальний прогрес-бар"""
    if total == 0:
        return "░" * length + " 0%"
    filled = int(length * current / total)
    empty = length - filled
    bar = '█' * filled + '░' * empty
    percentage = int(100 * current / total)
    return f"{bar} {percentage}%"

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("📋 Чек-лист документів")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ============================================================================
# DECLARATION FORM HANDLERS
# ============================================================================

async def declaration_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка натискання кнопки 'Анкета декларації'"""
    query = update.callback_query
    await query.answer()

    client, _ = get_active_client(update, context)

    if not client:
        await query.edit_message_text("❌ Спочатку зареєструйтесь: /start")
        return ConversationHandler.END

    # Перевіряємо чи вже є заповнена анкета
    declaration = db.get_declaration(client['id'])
    if declaration and declaration['status'] == 'completed':
        completed_at = declaration['completed_at'].strftime('%d.%m.%Y %H:%M')
        await query.edit_message_text(
            f"✅ <b>Ви вже заповнили анкету декларації</b>\n\n"
            f"📅 Заповнено: {completed_at}\n\n"
            f"💡 Редагування анкети неможливе. Якщо потрібно внести зміни, "
            f"зв'яжіться з менеджером.",
            parse_mode='HTML'
        )
        return ConversationHandler.END

    # Показуємо привітання та інструкцію
    await query.edit_message_text(
        "📋 <b>Анкета для складання податкової декларації</b>\n\n"
        "Вам буде задано 17 питань про фінансову діяльність за 2022-2025 роки.\n\n"
        "⚠️ <b>Важливо:</b>\n"
        "• Відповідайте максимально детально\n"
        "• Деякі питання можна пропустити (буде вказано)\n"
        "• Ви побачите прогрес заповнення\n"
        "• Після завершення редагування неможливе\n\n"
        "📝 Готові розпочати?",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Почати заповнення", callback_data="decl_begin")],
            [InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)]
        ])
    )

    return DECL_START

async def declaration_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок заповнення анкети"""
    query = update.callback_query
    await query.answer()

    client, admin_id = get_active_client(update, context)

    # Отримуємо або створюємо запис декларації
    declaration = db.get_or_create_declaration(client['id'])

    # Знаходимо перше питання без відповіді (відновлюємо прогрес)
    current_question_index = 0
    for idx, question in enumerate(DECLARATION_QUESTIONS):
        answer = declaration.get(question['key'])
        # Якщо відповідь пуста або None - це наше поточне питання
        if not answer:
            current_question_index = idx
            break
    else:
        # Якщо всі питання мають відповіді, але анкета не завершена
        current_question_index = len(DECLARATION_QUESTIONS) - 1

    # Ініціалізуємо дані для conversation
    context.user_data['declaration_current_q'] = current_question_index
    context.user_data['declaration_id'] = declaration['id']

    if current_question_index > 0:
        await query.edit_message_text(
            f"🔄 Продовжуємо заповнення анкети...\n\n"
            f"Ви вже відповіли на {current_question_index} питань."
        )
    else:
        await query.edit_message_text("🚀 Розпочинаємо заповнення анкети...")

    # Показуємо поточне питання та повертаємо його стан
    return await declaration_ask_question(update, context)

async def declaration_ask_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати поточне питання"""
    q_index = context.user_data['declaration_current_q']

    # Якщо всі питання пройдено - завершуємо
    if q_index >= len(DECLARATION_QUESTIONS):
        await declaration_complete(update, context)
        return ConversationHandler.END

    question = DECLARATION_QUESTIONS[q_index]
    total_questions = len(DECLARATION_QUESTIONS)
    answered_count = q_index

    # Прогрес-бар
    progress_bar = get_progress_bar(answered_count, total_questions)

    # Формуємо текст питання
    message = (
        f"<b>Питання {q_index + 1} з {total_questions}</b>\n"
        f"{progress_bar}\n\n"
        f"{question['emoji']} <b>{question['question']}</b>\n"
    )

    if question.get('hint'):
        message += f"\n💡 {question['hint']}\n"

    if not question['required']:
        message += "\n<i>✓ Це питання можна пропустити</i>"

    # Кнопки
    buttons = []
    if not question['required']:
        buttons.append([InlineKeyboardButton("⏭ Пропустити", callback_data=CALLBACK_DECL_SKIP)])

    # Навігація
    nav_buttons = []
    if q_index > 0:
        # Показуємо "Попереднє питання" тільки якщо не на першому питанні
        nav_buttons.append(InlineKeyboardButton("⬅️ Попереднє", callback_data=CALLBACK_DECL_PREVIOUS))
    nav_buttons.append(InlineKeyboardButton("🏠 Головне меню", callback_data=CALLBACK_DECL_MENU))

    buttons.append(nav_buttons)

    keyboard = InlineKeyboardMarkup(buttons)

    # Відправляємо питання
    if update.callback_query:
        sent_msg = await update.callback_query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        sent_msg = await update.message.reply_text(
            message,
            parse_mode='HTML',
            reply_markup=keyboard
        )

    # Зберігаємо message_id щоб видалити пізніше
    context.user_data['last_question_message_id'] = sent_msg.message_id

    return DECL_QUESTION if question.get('type') != 'files' else DECL_FILES

async def declaration_receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстової відповіді"""
    client, admin_id = get_active_client(update, context)
    q_index = context.user_data.get('declaration_current_q')

    # Якщо немає даних про поточне питання - conversation вже завершено
    if q_index is None:
        return ConversationHandler.END

    question = DECLARATION_QUESTIONS[q_index]

    # Якщо це питання з файлами - переходимо до обробки файлів
    if question.get('type') == 'files':
        return await declaration_handle_files(update, context)

    # Отримуємо відповідь
    answer = update.message.text.strip()

    if not answer:
        await update.message.reply_text("❌ Будь ласка, введіть відповідь або пропустіть питання.")
        return DECL_QUESTION

    # Видаляємо попереднє питання та відповідь користувача
    try:
        # Видаляємо питання
        last_q_msg_id = context.user_data.get('last_question_message_id')
        if last_q_msg_id:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=last_q_msg_id
            )
        # Видаляємо відповідь користувача
        await update.message.delete()
    except Exception as e:
        logger.error(f"Error deleting messages: {e}")

    # Зберігаємо відповідь у БД
    db.update_declaration_answer(client['id'], question['key'], answer)

    # Логуємо
    db.log_notification(
        client_id=client['id'],
        notification_type='declaration_answer',
        message=f"Відповідь на питання {q_index + 1}: {question['question'][:50]}...",
        admin_telegram_id=admin_id
    )

    # Переходимо до наступного питання
    context.user_data['declaration_current_q'] += 1
    return await declaration_ask_question(update, context)

async def declaration_handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка питання з файлами (Q15)"""
    client, admin_id = get_active_client(update, context)
    q_index = context.user_data['declaration_current_q']
    question = DECLARATION_QUESTIONS[q_index]

    # Ініціалізуємо список файлів якщо потрібно
    if 'declaration_files' not in context.user_data:
        context.user_data['declaration_files'] = []

    # Якщо це callback (Skip або Done)
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        if query.data == CALLBACK_DECL_SKIP:
            # Пропускаємо питання з файлами - зберігаємо "ПРОПУЩЕНО"
            db.update_declaration_answer(client['id'], question['key'], "ПРОПУЩЕНО")

            # Логуємо
            db.log_notification(
                client_id=client['id'],
                notification_type='declaration_answer',
                message=f"Питання {q_index + 1} (файли) пропущено",
                admin_telegram_id=admin_id
            )

            context.user_data['declaration_current_q'] += 1
            context.user_data.pop('declaration_files', None)
            return await declaration_ask_question(update, context)

        elif query.data == CALLBACK_DONE:
            # Зберігаємо файли як JSON
            files_data = context.user_data.get('declaration_files', [])
            if files_data:
                import json
                db.update_declaration_answer(
                    client['id'],
                    question['key'],
                    json.dumps(files_data, ensure_ascii=False)
                )

            # Переходимо до наступного питання
            context.user_data['declaration_current_q'] += 1
            context.user_data.pop('declaration_files', None)
            return await declaration_ask_question(update, context)

    # Якщо це файл
    if update.message and update.message.document:
        file = update.message.document

        try:
            # Завантажуємо файл
            tg_file = await context.bot.get_file(file.file_id)
            temp_path = os.path.join(tempfile.gettempdir(), file.file_name)
            await tg_file.download_to_drive(temp_path)

            # Отримуємо або створюємо папку клієнта
            folders = drive.create_client_folder_structure(client['full_name'], client['phone'])

            # Перевіряємо що папка клієнта існує
            if not folders or 'client' not in folders or not folders['client']:
                raise Exception("Не вдалося знайти або створити папку клієнта на Drive")

            parent_folder_id = folders['client']['id']

            # Шукаємо папку "Декларація" всередині папки клієнта
            existing_folders = drive.service.files().list(
                q=f"name='Декларація' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields='files(id, name)'
            ).execute().get('files', [])

            if existing_folders:
                declaration_folder_id = existing_folders[0]['id']
            else:
                # Створюємо папку "Декларація" якщо її немає
                folder_metadata = {
                    'name': 'Декларація',
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [parent_folder_id]
                }
                folder = drive.service.files().create(
                    body=folder_metadata,
                    fields='id'
                ).execute()
                declaration_folder_id = folder['id']
                logger.info(f"Created 'Декларація' folder for client {client['full_name']}")

            # Завантажуємо файл
            drive_file = drive.upload_file(temp_path, declaration_folder_id, file.file_name)

            # Додаємо до списку
            context.user_data['declaration_files'].append({
                'file_name': file.file_name,
                'drive_file_id': drive_file['id'],
                'drive_url': drive_file['webViewLink']
            })

            os.remove(temp_path)

            # Показуємо статус
            files_count = len(context.user_data['declaration_files'])
            await update.message.reply_text(
                f"✅ Файл завантажено: {file.file_name}\n\n"
                f"📊 Завантажено файлів: {files_count}\n\n"
                f"💡 Надішліть ще файли або натисніть \"Готово\"",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Готово", callback_data=CALLBACK_DONE),
                    InlineKeyboardButton("⏭ Пропустити", callback_data=CALLBACK_DECL_SKIP)
                ]])
            )

        except Exception as e:
            logger.error(f"Error uploading declaration file: {e}")
            await update.message.reply_text(
                f"❌ Помилка завантаження файлу: {str(e)}\n"
                f"Спробуйте ще раз."
            )

        return DECL_FILES

    # Якщо це текстове повідомлення замість файлу
    if update.message and update.message.text:
        await update.message.reply_text(
            "📎 Це питання потребує завантаження файлів.\n\n"
            "Надішліть документи або натисніть \"Пропустити\"",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустити", callback_data=CALLBACK_DECL_SKIP)
            ]])
        )
        return DECL_FILES

    return DECL_FILES

async def declaration_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пропустити необов'язкове питання"""
    query = update.callback_query
    await query.answer()

    client, admin_id = get_active_client(update, context)
    q_index = context.user_data.get('declaration_current_q')

    # Якщо немає даних про поточне питання - щось пішло не так
    if q_index is None:
        await query.answer("❌ Помилка. Спробуйте почати заново.", show_alert=True)
        return ConversationHandler.END

    question = DECLARATION_QUESTIONS[q_index]

    if question['required']:
        await query.answer("❌ Це питання обов'язкове!", show_alert=True)
        return DECL_QUESTION

    # Зберігаємо "ПРОПУЩЕНО" в БД щоб при поверненні не питати знову
    db.update_declaration_answer(client['id'], question['key'], "ПРОПУЩЕНО")

    # Логуємо
    db.log_notification(
        client_id=client['id'],
        notification_type='declaration_answer',
        message=f"Питання {q_index + 1} пропущено",
        admin_telegram_id=admin_id
    )

    # Переходимо до наступного питання
    context.user_data['declaration_current_q'] += 1
    return await declaration_ask_question(update, context)

async def declaration_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершення анкети та створення файлу"""
    client, admin_id = get_active_client(update, context)

    # Показуємо проміжне повідомлення про збереження
    if update.callback_query:
        saving_msg = await update.callback_query.edit_message_text("💾 Зберігаємо відповіді...")
    else:
        saving_msg = await update.message.reply_text("💾 Зберігаємо відповіді...")

    # Отримуємо всі відповіді
    declaration = db.get_declaration(client['id'])

    # Формуємо текстовий файл з відповідями
    content = f"АНКЕТА ДЛЯ СКЛАДАННЯ ПОДАТКОВОЇ ДЕКЛАРАЦІЇ\n"
    content += f"Клієнт: {client['full_name']}\n"
    content += f"Телефон: {client['phone']}\n"
    content += f"Дата заповнення: {declaration['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
    content += "=" * 80 + "\n\n"

    for idx, question in enumerate(DECLARATION_QUESTIONS, 1):
        key = question['key']
        answer = declaration.get(key, '')

        content += f"{idx}. {question['question']}\n"

        if answer and answer != "ПРОПУЩЕНО":
            if question.get('type') == 'files':
                # Якщо це файли - розпарсимо JSON
                try:
                    import json
                    files = json.loads(answer)
                    content += "Файли:\n"
                    for file_info in files:
                        content += f"  - {file_info['file_name']}: {file_info['drive_url']}\n"
                except:
                    content += f"{answer}\n"
            else:
                content += f"{answer}\n"
        else:
            content += "(Пропущено)\n"

        content += "\n"

    # Зберігаємо файл на Drive
    try:
        # Створюємо тимчасовий файл
        temp_path = os.path.join(tempfile.gettempdir(), f"Анкета_{client['full_name']}.txt")
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Отримуємо або створюємо папку клієнта та підпапку "Декларація"
        folders = drive.create_client_folder_structure(client['full_name'], client['phone'])

        # Перевіряємо що папка клієнта існує
        if not folders or 'client' not in folders or not folders['client']:
            raise Exception("Не вдалося знайти або створити папку клієнта на Drive")

        parent_folder_id = folders['client']['id']

        # Шукаємо папку "Декларація" всередині папки клієнта
        existing_folders = drive.service.files().list(
            q=f"name='Декларація' and '{parent_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)'
        ).execute().get('files', [])

        if existing_folders:
            declaration_folder_id = existing_folders[0]['id']
        else:
            # Створюємо папку "Декларація" якщо її немає
            folder_metadata = {
                'name': 'Декларація',
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            folder = drive.service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            declaration_folder_id = folder['id']
            logger.info(f"Created 'Декларація' folder for client {client['full_name']}")

        # Завантажуємо файл
        file_name = f"Анкета_{client['full_name']}.txt"
        drive.upload_file(temp_path, declaration_folder_id, file_name)
        os.remove(temp_path)

        # Оновлюємо статус декларації
        db.complete_declaration(client['id'])

        # Логуємо
        db.log_notification(
            client_id=client['id'],
            notification_type='declaration_completed',
            message=f"Анкету декларації завершено",
            admin_telegram_id=admin_id
        )

        # Відправляємо нотифікацію адмінам
        await notify_admins(
            f"📋 Клієнт завершив анкету декларації!\n\n"
            f"👤 {client['full_name']}\n"
            f"📱 {client['phone']}\n"
            f"📊 Статус: {client['status']}\n"
            f"📁 <a href=\"{client['drive_folder_url']}\">Відкрити папку на Drive</a>"
        )

        # Повідомлення про завершення та автоматичний показ чек-листа
        completion_message = (
            f"✅ <b>Анкету успішно заповнено!</b>\n\n"
            f"📁 Відповіді збережено\n\n"
            f"Дякуємо за відповіді! Наш менеджер опрацює інформацію "
            f"та зв'яжеться з вами найближчим часом."
        )

        # Видаляємо повідомлення про збереження та показуємо завершення
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=saving_msg.message_id
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=completion_message,
            parse_mode='HTML'
        )

        # Очищаємо дані conversation ПЕРЕД показом чек-листа
        context.user_data.pop('declaration_current_q', None)
        context.user_data.pop('declaration_id', None)
        context.user_data.pop('declaration_files', None)

        # Автоматично показуємо чек-лист (як після завантаження документів)
        import asyncio
        await asyncio.sleep(0.5)
        await show_checklist(update, context, force_new_message=True)

        # Повертаємо END щоб завершити conversation ПЕРЕД показом чек-листа
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error completing declaration: {e}")
        error_message = "❌ Помилка збереження анкети. Зв'яжіться з менеджером."

        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=saving_msg.message_id
            )
        except:
            pass

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error_message
        )

    # Якщо була помилка, також очищаємо дані і завершуємо conversation
    context.user_data.pop('declaration_current_q', None)
    context.user_data.pop('declaration_id', None)
    context.user_data.pop('declaration_files', None)

    return ConversationHandler.END

async def declaration_previous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повернутися до попереднього питання"""
    query = update.callback_query
    await query.answer()

    # Переходимо до попереднього питання
    current_q = context.user_data.get('declaration_current_q', 0)
    if current_q > 0:
        context.user_data['declaration_current_q'] = current_q - 1

    # Показуємо попереднє питання
    return await declaration_ask_question(update, context)

async def declaration_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вийти в головне меню зі збереженням прогресу"""
    query = update.callback_query
    await query.answer("💾 Прогрес збережено!")

    # НЕ очищаємо дані - прогрес зберігається в БД
    # Просто очищаємо тимчасові дані conversation
    context.user_data.pop('declaration_current_q', None)
    context.user_data.pop('declaration_files', None)

    # Повертаємося до чек-листа
    await show_checklist(update, context)

    return ConversationHandler.END

async def declaration_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повернутися назад до чек-листа (з початкового екрану)"""
    query = update.callback_query
    await query.answer()

    # Очищаємо дані анкети
    context.user_data.pop('declaration_current_q', None)
    context.user_data.pop('declaration_id', None)
    context.user_data.pop('declaration_files', None)

    # Повертаємося до чек-листа
    await show_checklist(update, context)

    return ConversationHandler.END

async def declaration_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасування заповнення анкети"""
    await update.message.reply_text(
        "❌ Заповнення анкети скасовано.\n\n"
        "Ви можете повернутися до неї пізніше.",
        reply_markup=get_main_keyboard()
    )

    context.user_data.pop('declaration_current_q', None)
    context.user_data.pop('declaration_id', None)
    context.user_data.pop('declaration_files', None)

    return ConversationHandler.END

# ============================================================================
# ADMIN COMMANDS
# ============================================================================

async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /login +380XXXXXXXXX - увійти як клієнт"""
    admin_id = update.effective_user.id

    # Перевіряємо чи це адмін
    if admin_id not in load_admins():
        await update.message.reply_text("❌ У вас немає доступу до адмін-панелі")
        return

    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "❌ Невірний формат\n\n"
            "Використання: /login +380XXXXXXXXX\n"
            "Приклад: /login +380501234567"
        )
        return

    phone = normalize_phone(context.args[0].strip())
    client = db.get_client_by_phone(phone)

    if not client:
        await update.message.reply_text(
            f"❌ Клієнт з номером {phone} не знайдений.\n\n"
            f"Для створення нового клієнта:\n"
            f"/register {phone} ПІБ_клієнта\n\n"
            f"Приклад: /register {phone} Іваненко Андрій Васильович"
        )
        return

    # Зберігаємо сесію в context
    context.user_data['admin_mode'] = {
        'client_id': client['id'],
        'client_phone': phone,
        'admin_telegram_id': admin_id
    }

    uploaded_types = db.get_uploaded_types(client['id'])
    required_count = len(REQUIRED_DOCUMENTS)
    uploaded_required = sum(1 for doc in REQUIRED_DOCUMENTS if doc in uploaded_types)

    await update.message.reply_text(
        f"✅ <b>Увійшли в режим адміністратора</b>\n\n"
        f"👤 Клієнт: {client['full_name']}\n"
        f"📱 Телефон: {client['phone']}\n"
        f"📊 Прогрес: {uploaded_required}/{required_count} документів\n"
        f"📁 <a href=\"{client['drive_folder_url']}\">Папка на Drive</a>\n\n"
        f"💬 <i>Уведомлення від інших клієнтів продовжують приходити</i>\n\n"
        f"Щоб вийти: /logout",
        parse_mode='HTML',
        disable_web_page_preview=True
    )

    # Показуємо чеклист
    await show_checklist(update, context, force_new_message=True)

async def admin_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /register +380XXXXXXXXX ПІБ - створити нового клієнта"""
    admin_id = update.effective_user.id

    if admin_id not in load_admins():
        await update.message.reply_text("❌ У вас немає доступу")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ Невірний формат\n\n"
            "Використання: /register +380XXXXXXXXX ПІБ\n"
            "Приклад: /register +380501234567 Іваненко Андрій Васильович"
        )
        return

    phone = normalize_phone(context.args[0].strip())
    full_name = ' '.join(context.args[1:])

    # Перевіряємо чи не існує вже
    existing = db.get_client_by_phone(phone)
    if existing:
        await update.message.reply_text(f"❌ Клієнт з номером {phone} вже існує")
        return

    # Створюємо клієнта (telegram_id = 0 для адмін-створених)
    client = db.create_client(
        telegram_id=0,
        full_name=full_name,
        phone=phone
    )

    try:
        # Створюємо папки на Drive
        folders = drive.create_client_folder_structure(full_name, phone)
        db.update_client_drive_folder(client['id'], folders['client']['id'], folders['client']['webViewLink'])

        # Логуємо
        db.log_notification(
            client_id=client['id'],
            notification_type='admin_registered_client',
            message=f"Адмін зареєстрував клієнта: {full_name}, {phone}",
            admin_telegram_id=admin_id
        )

        # Одразу входимо в режим адміна
        context.user_data['admin_mode'] = {
            'client_id': client['id'],
            'client_phone': phone,
            'admin_telegram_id': admin_id
        }

        await update.message.reply_text(
            f"✅ <b>Клієнт створений і ви увійшли в режим адміністратора</b>\n\n"
            f"👤 {full_name}\n"
            f"📱 {phone}\n"
            f"📁 <a href=\"{folders['client']['webViewLink']}\">Папка на Drive</a>",
            parse_mode='HTML',
            disable_web_page_preview=True
        )

        await notify_admins(
            f"🆕 Адмін створив нового клієнта\n\n"
            f"👤 {full_name}\n"
            f"📱 {phone}\n"
            f"👨‍💼 Адмін ID: {admin_id}\n"
            f"📊 Статус: in_progress (0/9 документів)\n"
            f"📁 <a href=\"{folders['client']['webViewLink']}\">Відкрити папку на Drive</a>"
        )

        # Показуємо чеклист
        await show_checklist(update, context, force_new_message=True)

    except Exception as e:
        logger.error(f"Failed to create client: {e}")
        await update.message.reply_text(f"❌ Помилка створення клієнта: {str(e)}")

async def admin_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /logout - вийти з режиму адміністратора"""
    if 'admin_mode' in context.user_data:
        client_phone = context.user_data['admin_mode']['client_phone']
        context.user_data.pop('admin_mode')

        await update.message.reply_text(
            f"✅ Вийшли з облікового запису клієнта {client_phone}\n\n"
            f"Ви знову в звичайному режимі адміністратора."
        )
    else:
        await update.message.reply_text("⚠️ Ви не в режимі адміністратора")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /info +380XXXXXXXXX - проверить какие документы загрузил клиент"""
    if not context.args or len(context.args) == 0:
        await update.message.reply_text(
            "❌ Неверный формат\n\n"
            "Использование: /info +380XXXXXXXXX\n"
            "Пример: /info +380501234567"
        )
        return

    phone = context.args[0].strip()

    # Получаем клиента по номеру телефона
    client = db.get_client_by_phone(phone)
    if not client:
        await update.message.reply_text(f"❌ Клиент с номером {phone} не найден")
        return

    # Получаем все документы клиента
    documents = db.get_documents_by_client(client['id'])

    # Группируем документы по типам
    uploaded_types = {doc['document_type'] for doc in documents}

    # Формируем сообщение
    message = f"📊 Информация о клиенте:\n\n"
    message += f"👤 ФИО: {client['full_name']}\n"
    message += f"📱 Телефон: {client['phone']}\n"
    message += f"📅 Регистрация: {client['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
    message += f"🔄 Статус: {client['status']}\n\n"

    if client['drive_folder_url']:
        message += f"📁 <a href=\"{client['drive_folder_url']}\">Папка на Google Drive</a>\n\n"

    message += "📋 Чек-лист документов:\n\n"

    for doc_type, doc_info in DOCUMENT_TYPES.items():
        emoji = doc_info['emoji']
        name = doc_info['name']

        if doc_type in uploaded_types:
            # Подсчитываем количество файлов этого типа
            count = sum(1 for doc in documents if doc['document_type'] == doc_type)
            message += f"✅ {emoji} {name} ({count} шт.)\n"
        else:
            message += f"❌ {emoji} {name}\n"

    # Статистика
    total_types = len(DOCUMENT_TYPES)
    uploaded_count = len(uploaded_types)
    total_files = len(documents)

    message += f"\n📈 Прогресс: {uploaded_count}/{total_types} типов документов\n"
    message += f"📎 Всего файлов: {total_files}"

    await update.message.reply_text(message, parse_mode='HTML', disable_web_page_preview=True)

# ============================================================================
# MAIN
# ============================================================================

def main():
    global notification_bot

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    if NOTIFICATION_BOT_TOKEN:
        from telegram import Bot
        notification_bot = Bot(token=NOTIFICATION_BOT_TOKEN)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
            WAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone),
                MessageHandler(filters.CONTACT, receive_phone)
            ]
        },
        fallbacks=[CommandHandler('start', start)]
    )

    # Declaration form conversation handler
    declaration_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(declaration_start, pattern=f"^{CALLBACK_DECL_START}$")
        ],
        states={
            DECL_START: [
                CallbackQueryHandler(declaration_begin, pattern=f"^decl_begin$"),
                CallbackQueryHandler(declaration_back, pattern=f"^{CALLBACK_BACK}$")
            ],
            DECL_QUESTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, declaration_receive_answer),
                CallbackQueryHandler(declaration_skip, pattern=f"^{CALLBACK_DECL_SKIP}$"),
                CallbackQueryHandler(declaration_previous, pattern=f"^{CALLBACK_DECL_PREVIOUS}$"),
                CallbackQueryHandler(declaration_menu, pattern=f"^{CALLBACK_DECL_MENU}$")
            ],
            DECL_FILES: [
                MessageHandler(filters.Document.ALL, declaration_handle_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, declaration_handle_files),
                CallbackQueryHandler(declaration_handle_files, pattern=f"^{CALLBACK_DONE}$"),
                CallbackQueryHandler(declaration_handle_files, pattern=f"^{CALLBACK_DECL_SKIP}$"),
                CallbackQueryHandler(declaration_previous, pattern=f"^{CALLBACK_DECL_PREVIOUS}$"),
                CallbackQueryHandler(declaration_menu, pattern=f"^{CALLBACK_DECL_MENU}$")
            ]
        },
        fallbacks=[CommandHandler('cancel', declaration_cancel)],
        per_message=False
    )

    # Additional docs conversation handler (для кастомних назв)
    additional_docs_handler = ConversationHandler(
        entry_points=[
            MessageHandler(
                (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
                handle_file_upload
            )
        ],
        states={
            ADDITIONAL_DOC_WAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_additional_doc_name)
            ]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: show_checklist(u, c, force_new_message=True))],
        per_message=False,
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_handler(declaration_handler)
    application.add_handler(additional_docs_handler)
    # Admin commands
    application.add_handler(CommandHandler('login', admin_login))
    application.add_handler(CommandHandler('register', admin_register))
    application.add_handler(CommandHandler('logout', admin_logout))
    application.add_handler(CommandHandler('info', info_command))
    application.add_handler(CallbackQueryHandler(handle_upload_request, pattern=f"^{CALLBACK_UPLOAD_PREFIX}"))
    application.add_handler(CallbackQueryHandler(handle_done, pattern=f"^{CALLBACK_DONE}$"))
    application.add_handler(CallbackQueryHandler(handle_back, pattern=f"^{CALLBACK_BACK}$"))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^📋"),
        lambda u, c: show_checklist(u, c)
    ))
    # Обработчик текстовых сообщений (для пароля ЕЦП)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex("^📋"),
        handle_text_message
    ))

    # Створюємо таблицю для нагадувань (якщо не існує)
    try:
        db.create_reminders_table()
        logger.info("Reminders table created/verified")
    except Exception as e:
        logger.error(f"Error creating reminders table: {e}")

    # Налаштовуємо JobQueue для щоденної перевірки неактивних клієнтів
    job_queue = application.job_queue

    # Запускаємо перевірку щодня о 14:03 за київським часом
    import datetime as dt
    kyiv_tz = pytz.timezone('Europe/Kiev')
    check_time = dt.time(hour=14, minute=23, tzinfo=kyiv_tz)

    job_queue.run_daily(
        check_and_send_reminders,
        time=check_time,
        days=(0, 1, 2, 3, 4, 5, 6),  # Всі дні тижня
        name="daily_reminder_check"
    )

    logger.info("Daily reminder job scheduled for 14:03 Kyiv time")

    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
