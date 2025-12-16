"""
AI Document Validator
Модуль для перевірки документів за допомогою GPT-4 Vision
"""

import os
import json
import base64
import logging
from typing import Dict, Optional, Tuple
from PIL import Image
from io import BytesIO
import openai

from prompts import DOCUMENT_PROMPTS, DOCUMENT_TYPE_TO_PROMPT, REJECTION_REASONS

logger = logging.getLogger(__name__)

# ============================================================================
# НАЛАШТУВАННЯ
# ============================================================================

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
AI_VALIDATION_ENABLED = os.getenv('AI_VALIDATION_ENABLED', 'true').lower() == 'true'

# Ініціалізація OpenAI клієнта
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    logger.warning("OPENAI_API_KEY not set - AI validation will be disabled")

# ============================================================================
# VALIDATION РЕЗУЛЬТАТИ
# ============================================================================

class ValidationResult:
    """Результат AI-перевірки документа"""

    def __init__(
        self,
        status: str,  # 'accepted', 'rejected', 'uncertain'
        error_code: Optional[str] = None,
        reason: Optional[str] = None,
        ai_response: Optional[Dict] = None
    ):
        self.status = status
        self.error_code = error_code
        self.reason = reason
        self.ai_response = ai_response or {}

    def is_accepted(self) -> bool:
        return self.status == 'accepted'

    def is_rejected(self) -> bool:
        return self.status == 'rejected'

    def is_uncertain(self) -> bool:
        return self.status == 'uncertain'

    def get_user_message(self) -> str:
        """Отримати повідомлення для користувача"""
        if self.is_accepted():
            return "✅ Документ успішно перевірено і завантажено!"

        elif self.is_rejected():
            # Отримуємо читабельну причину з REJECTION_REASONS
            reason_text = REJECTION_REASONS.get(
                self.error_code,
                "❌ Документ не відповідає вимогам"
            )
            return (
                f"⚠️ Документ не пройшов перевірку:\n\n"
                f"{reason_text}\n\n"
                f"Будь ласка, перегляньте інструкцію та завантажте правильний документ."
            )

        elif self.is_uncertain():
            return "✅ Документ завантажено! Наш спеціаліст перевірить його найближчим часом."

        return "✅ Документ завантажено!"

    def to_dict(self) -> Dict:
        """Конвертувати в словник для збереження в БД"""
        return {
            'status': self.status,
            'error_code': self.error_code,
            'reason': self.reason,
            'ai_response': self.ai_response
        }

# ============================================================================
# AI DOCUMENT VALIDATOR
# ============================================================================

class AIDocumentValidator:
    """Валідатор документів за допомогою GPT-4 Vision"""

    def __init__(self):
        self.enabled = AI_VALIDATION_ENABLED and OPENAI_API_KEY is not None

    def validate_document(
        self,
        file_path: str,
        document_type: str
    ) -> Optional[ValidationResult]:
        """
        Перевірити документ за допомогою AI

        Args:
            file_path: Шлях до файлу
            document_type: Тип документа (ключ з DOCUMENT_TYPES)

        Returns:
            ValidationResult або None якщо валідація вимкнена/не потрібна
        """
        # Перевіряємо чи увімкнена валідація
        if not self.enabled:
            logger.info("AI validation is disabled")
            return None

        # Перевіряємо чи потрібна валідація для цього типу документа
        prompt_key = DOCUMENT_TYPE_TO_PROMPT.get(document_type)
        if prompt_key is None:
            logger.info(f"AI validation not needed for document type: {document_type}")
            return None

        try:
            # Завантажуємо промпт
            prompt = DOCUMENT_PROMPTS.get(prompt_key)
            if not prompt:
                logger.error(f"Prompt not found for document type: {document_type}")
                return None

            # Перевіряємо чи це зображення
            if not self._is_image_file(file_path):
                logger.info(f"File is not an image, skipping AI validation: {file_path}")
                return None

            # Конвертуємо файл в base64
            base64_image = self._encode_image(file_path)
            if not base64_image:
                logger.error(f"Failed to encode image: {file_path}")
                return ValidationResult(
                    status='uncertain',
                    error_code='damaged_file',
                    reason='Не вдалося обробити файл'
                )

            # Викликаємо GPT-4 Vision
            logger.info(f"Calling GPT-4 Vision for document type: {document_type}")
            ai_response = self._call_gpt4_vision(prompt, base64_image)

            # Парсимо відповідь
            result = self._parse_ai_response(ai_response)
            logger.info(f"AI validation result: {result.status} (error_code: {result.error_code})")

            return result

        except Exception as e:
            logger.error(f"Error during AI validation: {e}", exc_info=True)
            # При помилці вважаємо документ UNCERTAIN (потрібна ручна перевірка)
            return ValidationResult(
                status='uncertain',
                error_code='uncertain',
                reason=f'Помилка AI-перевірки: {str(e)}'
            )

    def _is_image_file(self, file_path: str) -> bool:
        """Перевірити чи є файл зображенням"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        _, ext = os.path.splitext(file_path.lower())
        return ext in image_extensions

    def _encode_image(self, file_path: str) -> Optional[str]:
        """
        Конвертувати зображення в base64

        Також намагається оптимізувати розмір якщо файл занадто великий
        """
        try:
            # Відкриваємо зображення
            with Image.open(file_path) as img:
                # Конвертуємо в RGB якщо потрібно
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')

                # Оптимізуємо розмір якщо потрібно (GPT-4 Vision має ліміт)
                max_size = (2048, 2048)
                if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    logger.info(f"Resized image to {img.size}")

                # Конвертуємо в base64
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                image_bytes = buffer.getvalue()
                base64_image = base64.b64encode(image_bytes).decode('utf-8')

                return base64_image

        except Exception as e:
            logger.error(f"Error encoding image: {e}")
            return None

    def _call_gpt4_vision(self, prompt: str, base64_image: str) -> Dict:
        """
        Викликати GPT-4 Vision API

        Args:
            prompt: Текст промпта
            base64_image: Зображення в base64

        Returns:
            Відповідь від API у форматі JSON
        """
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",  # GPT-4 Turbo with Vision
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"  # Висока деталізація для документів
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000,
                temperature=0.1,  # Низька temperature для більш детермінованих результатів
                response_format={"type": "json_object"}  # Примусово JSON відповідь
            )

            # Отримуємо текст відповіді
            content = response.choices[0].message.content
            logger.debug(f"GPT-4 Vision raw response: {content}")

            # Парсимо JSON
            result = json.loads(content)
            return result

        except Exception as e:
            logger.error(f"Error calling GPT-4 Vision API: {e}")
            raise

    def _parse_ai_response(self, ai_response: Dict) -> ValidationResult:
        """
        Парсити відповідь від AI та конвертувати в ValidationResult

        Логіка:
        1. Якщо quality_check = FAILED → REJECTED
        2. Якщо document_check.status = REJECTED → REJECTED
        3. Якщо document_check.status = UNCERTAIN → UNCERTAIN
        4. Якщо document_check.status = ACCEPTED → ACCEPTED
        """
        try:
            quality_check = ai_response.get('quality_check', 'PASSED')
            error_code = ai_response.get('error_code')
            document_check = ai_response.get('document_check', {})
            doc_status = document_check.get('status', 'UNCERTAIN')
            doc_error_code = document_check.get('error_code')

            # Пріоритет для error_code: спочатку з document_check, потім загальний
            final_error_code = doc_error_code or error_code

            # ЕТАП 1: Перевірка якості
            if quality_check == 'FAILED':
                return ValidationResult(
                    status='rejected',
                    error_code=final_error_code or 'poor_quality',
                    reason='Документ не пройшов перевірку якості',
                    ai_response=ai_response
                )

            # ЕТАП 2: Перевірка типу документа
            if doc_status == 'REJECTED':
                return ValidationResult(
                    status='rejected',
                    error_code=final_error_code or 'wrong_document',
                    reason='Невірний тип документа',
                    ai_response=ai_response
                )

            elif doc_status == 'UNCERTAIN':
                return ValidationResult(
                    status='uncertain',
                    error_code=final_error_code or 'uncertain',
                    reason='Документ потребує ручної перевірки',
                    ai_response=ai_response
                )

            elif doc_status == 'ACCEPTED':
                return ValidationResult(
                    status='accepted',
                    error_code=None,
                    reason='Документ прийнято',
                    ai_response=ai_response
                )

            else:
                # Невідомий статус - вважаємо UNCERTAIN
                logger.warning(f"Unknown document status: {doc_status}")
                return ValidationResult(
                    status='uncertain',
                    error_code='uncertain',
                    reason='Невідомий статус перевірки',
                    ai_response=ai_response
                )

        except Exception as e:
            logger.error(f"Error parsing AI response: {e}", exc_info=True)
            # При помилці парсингу - UNCERTAIN
            return ValidationResult(
                status='uncertain',
                error_code='uncertain',
                reason=f'Помилка обробки відповіді AI: {str(e)}',
                ai_response=ai_response
            )

# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

# Створюємо єдиний екземпляр валідатора
validator = AIDocumentValidator()
