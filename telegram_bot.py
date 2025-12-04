"""
Telegram Bot для збору документів клієнтів
Все в одному файлі
"""
import os
import logging
import tempfile
import base64
import json
from datetime import datetime, timedelta
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
    'personal': 'Особисті документи'
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
        'required': True
    },
    'passport': {
        'name': 'Сканкопія паспорта та РНОКПП (ІПН)',
        'short': 'Паспорт',
        'emoji': '📕',
        'folder': 'personal',
        'required': True
    },
    'registration': {
        'name': 'Витяг з реєстру територіальної громади',
        'short': 'Склад сім\'ї',
        'description': 'Довідка про склад сім\'ї (витяг з реєстру територіальної громади)',
        'emoji': '🏠',
        'folder': 'personal',
        'required': True
    },
    'workbook': {
        'name': 'Копія трудової книжки',
        'short': 'Трудова книжка',
        'emoji': '📗',
        'folder': 'personal',
        'required': False
    },
    'credit_contracts': {
        'name': 'Кредитні договори',
        'short': 'Кредитні договори',
        'emoji': '📑',
        'folder': 'credit',
        'required': True,
        'multiple': True
    },
    'bank_statements': {
        'name': 'Виписки про залишок коштів на рахунках',
        'short': 'Виписки',
        'emoji': '🏦',
        'folder': 'personal',
        'required': True
    },
    'expenses': {
        'name': 'Підтвердження витрат за останні місяці',
        'short': 'Витрати',
        'emoji': '💰',
        'folder': 'personal',
        'required': True
    },
    'story': {
        'name': 'Ваша історія (у форматі Word)',
        'short': 'Історія',
        'emoji': '📝',
        'folder': 'personal',
        'required': True
    },
    'family_income': {
        'name': 'Доходи членів сім\'ї (довідка з податкової)',
        'short': 'Доходи членів сім\'ї',
        'emoji': '💵',
        'folder': 'personal',
        'required': False
    },
    'debt_certificates': {
        'name': 'Довідки про стан заборгованості',
        'short': 'Заборгованості',
        'emoji': '📋',
        'folder': 'personal',
        'required': True
    },
    'executive': {
        'name': 'Виписки по виконавчих провадженнях',
        'short': 'Виконавчі',
        'emoji': '⚖️',
        'folder': 'personal',
        'required': False
    }
}

REQUIRED_DOCUMENTS = [key for key, val in DOCUMENT_TYPES.items() if val.get('required', False)]

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
        self.conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        self.conn.autocommit = True

    def execute(self, query, params=None, fetch=False):
        try:
            with self.conn.cursor() as cur:
                cur.execute(query, params or ())
                if fetch:
                    return cur.fetchall() if cur.description else None
                return cur.rowcount
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
    def add_document(self, client_id, document_type, file_name, drive_file_id, drive_file_url, file_size):
        query = """
            INSERT INTO docbot.documents (client_id, document_type, file_name, drive_file_id, drive_file_url, file_size)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        result = self.execute(query, (client_id, document_type, file_name, drive_file_id, drive_file_url, file_size), fetch=True)
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
    def log_notification(self, client_id, notification_type, message):
        query = "INSERT INTO docbot.notifications_log (client_id, notification_type, message) VALUES (%s, %s, %s)"
        self.execute(query, (client_id, notification_type, message))

    def get_inactive_clients(self):
        query = """
            SELECT * FROM docbot.clients
            WHERE status = 'in_progress'
            AND last_activity < %s
        """
        cutoff_date = datetime.now() - timedelta(days=REMINDER_DAYS)
        return self.execute(query, (cutoff_date,), fetch=True)

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

        credit_folder = self.get_or_create_folder(SUBFOLDERS['credit'], client_folder['id'])
        personal_folder = self.get_or_create_folder(SUBFOLDERS['personal'], client_folder['id'])

        return {
            'client': client_folder,
            'credit': credit_folder,
            'personal': personal_folder
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

# Callback data
CALLBACK_UPLOAD_PREFIX = "upload_"
CALLBACK_DONE = "done"
CALLBACK_BACK = "back"

db = Database()
drive = DriveManager()
notification_bot = None

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

async def notify_admins(message):
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
                parse_mode='HTML'
            )
            logger.info(f"Notification sent to admin: {admin_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

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

    await update.message.reply_text(
        "Вітаю! 👋\n\n"
        "Я допоможу вам зібрати всі необхідні документи для списання боргів.\n\n"
        "🎁 <b>БОНУС:</b> При завершенні збору всіх документів ви отримаєте "
        "подарунок від нашої компанії!\n\n"
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
        f"🆔 Telegram: {update.effective_user.id}"
    )

    return ConversationHandler.END

async def show_checklist(update: Update, context: ContextTypes.DEFAULT_TYPE, force_new_message=False):
    query = update.callback_query
    user_id = update.effective_user.id

    client = db.get_client_by_telegram_id(user_id)
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

    message = f"📋 <b>Ваш прогрес: {uploaded_required_count}/{required_count} обов'язкових документів</b>\n\n"
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
        message += f"\n<b>Додаткові документи (необов'язкові):</b>\n"
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

    # Создаём кнопки и группируем их по 2 в строке
    buttons = []
    for doc_key, doc_info in DOCUMENT_TYPES.items():
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

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query and not force_new_message:
        await query.answer()
        await query.edit_message_text(message, parse_mode='HTML', reply_markup=reply_markup)
    else:
        # Отправляем новое сообщение (либо нет query, либо force_new_message=True)
        if query:
            await query.answer()
        await update.effective_chat.send_message(message, parse_mode='HTML', reply_markup=reply_markup)

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
            f"Після відправки натисніть кнопку \"✅ Готово\".",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Готово", callback_data=CALLBACK_DONE),
                InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)
            ]])
        )
    else:
        # Используем description если есть, иначе name
        doc_title = doc_info.get('description', doc_info['name'])
        message = f"{doc_info['emoji']} <b>{doc_title}</b>\n\n"
        if doc_info.get('multiple'):
            message += (
                f"📎 Надішліть файл(и) документів.\n"
                f"Ви можете надіслати кілька файлів (до 70).\n\n"
                f"Після завантаження всіх файлів натисніть кнопку \"✅ Готово\"."
            )
        else:
            message += (
                f"📎 Надішліть файл документа.\n\n"
                f"Після завантаження натисніть кнопку \"✅ Готово\"."
            )

        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Готово", callback_data=CALLBACK_DONE),
                InlineKeyboardButton("« Назад", callback_data=CALLBACK_BACK)
            ]])
        )

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений (только для пароля ЕЦП)"""
    user_id = update.effective_user.id
    client = db.get_client_by_telegram_id(user_id)

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
                f"🔐 Клієнт зберіг пароль від ЕЦП\\n\\n"
                f"👤 {client['full_name']}\\n"
                f"📱 {client['phone']}\\n"
                f"🔑 Пароль: {password}"
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
    user_id = update.effective_user.id
    client = db.get_client_by_telegram_id(user_id)

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

    # Показываем сообщение о загрузке только для первого файла
    if 'uploaded_files' not in context.user_data or len(context.user_data['uploaded_files']) == 0:
        loading_msg = await update.message.reply_text("⏳ Очікуйте, завантажуємо документи...")
    else:
        loading_msg = None

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

        folder_type = doc_info['folder']
        folders = drive.create_client_folder_structure(client['full_name'], client['phone'])
        target_folder_id = folders[folder_type]['id']

        # Загружаем с новым именем
        drive_file = drive.upload_file(temp_path, target_folder_id, new_file_name)

        db.add_document(
            client_id=client['id'],
            document_type=doc_key,
            file_name=new_file_name,
            drive_file_id=drive_file['id'],
            drive_file_url=drive_file['webViewLink'],
            file_size=int(drive_file.get('size', 0))
        )

        # Логируем в notifications_log
        db.log_notification(
            client_id=client['id'],
            notification_type='document_uploaded',
            message=f"Завантажено документ: {doc_info['name']} - {new_file_name}"
        )

        db.update_last_activity(client['id'])
        os.remove(temp_path)

        if 'uploaded_files' not in context.user_data:
            context.user_data['uploaded_files'] = []
        context.user_data['uploaded_files'].append(new_file_name)

        # Удаляем сообщение о загрузке только если оно было создано
        if loading_msg:
            await loading_msg.delete()

        # Формируем ОДНО сообщение со списком всех загруженных файлов
        uploaded_files = context.user_data['uploaded_files']
        count = len(uploaded_files)

        message = ""
        for file_name in uploaded_files:
            message += f"✅ Файл завантажено: {file_name}\n"

        message += f"📊 Завантажено файлів: {count}\n\n"
        message += "Надішліть ще файли або натисніть \"✅ Готово\" для завершення."

        # Редактируем существующее сообщение или создаём новое
        if 'upload_status_message' in context.user_data:
            try:
                await context.user_data['upload_status_message'].edit_text(message)
            except:
                # Если не получилось отредактировать, создаём новое
                msg = await update.message.reply_text(message)
                context.user_data['upload_status_message'] = msg
        else:
            # Первый файл - создаём сообщение
            msg = await update.message.reply_text(message)
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

async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    client = db.get_client_by_telegram_id(user_id)

    if 'uploading_doc_type' not in context.user_data:
        await query.edit_message_text("⚠️ Немає активного завантаження")
        return

    doc_key = context.user_data['uploading_doc_type']
    doc_info = DOCUMENT_TYPES[doc_key]
    uploaded_count = len(context.user_data.get('uploaded_files', []))

    # Для обычных файлов (не пароль ЕЦП)
    if uploaded_count == 0:
        message = f"⚠️ Ви не завантажили жодного файлу для \"{doc_info['name']}\""
    else:
        # Показываем список загруженных файлов
        uploaded_files = context.user_data.get('uploaded_files', [])
        message = f"✅ Завантажено файлів: {uploaded_count}\n\n"
        message += "📎 <b>Список файлів:</b>\n"
        for idx, file_name in enumerate(uploaded_files, 1):
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

    message += f"\n\n📊 Прогрес: {required_uploaded}/{required_total} обов'язкових документів"

    if required_uploaded == required_total:
        db.update_client_status(client['id'], 'completed')

        # Логируем завершение сбора документов
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
            "Дякуємо за вашу наполегливість! 💪"
        )
        await notify_admins(
            f"🎉 Клієнт завершив збір документів!\n\n"
            f"👤 {client['full_name']}\n"
            f"📱 {client['phone']}"
        )
    else:
        message += "\n\nПродовжуйте - залишилось небагато! 🚀"
        await notify_admins(
            f"📄 Клієнт завантажив документ\n\n"
            f"👤 {client['full_name']}\n"
            f"📱 {client['phone']}\n"
            f"📑 {doc_info['name']}\n"
            f"📊 Прогрес: {required_uploaded}/{required_total}"
        )

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

def get_main_keyboard():
    keyboard = [[KeyboardButton("📋 Чек-лист документів")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ============================================================================
# ADMIN COMMANDS
# ============================================================================

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

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('info', info_command))
    application.add_handler(CallbackQueryHandler(handle_upload_request, pattern=f"^{CALLBACK_UPLOAD_PREFIX}"))
    application.add_handler(CallbackQueryHandler(handle_done, pattern=f"^{CALLBACK_DONE}$"))
    application.add_handler(CallbackQueryHandler(handle_back, pattern=f"^{CALLBACK_BACK}$"))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex("^📋"),
        lambda u, c: show_checklist(u, c)
    ))
    # Обработчик текстовых сообщений (для пароля ЕЦП) - ДОЛЖЕН быть перед обработчиком файлов
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex("^📋"),
        handle_text_message
    ))
    application.add_handler(MessageHandler(
        (filters.Document.ALL | filters.PHOTO) & ~filters.COMMAND,
        handle_file_upload
    ))

    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
