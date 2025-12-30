"""
AI Document Validator
Модуль для перевірки документів за допомогою GPT-4 Vision (images) + PDF (Responses API)
"""

import os
import json
import base64
import logging
import re
from typing import Dict, Optional, Tuple
from PIL import Image
from io import BytesIO

import openai  # legacy usage for chat.completions (images)

# New client for Responses API (PDF)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

from prompts import DOCUMENT_PROMPTS, DOCUMENT_TYPE_TO_PROMPT, REJECTION_REASONS

logger = logging.getLogger(__name__)

# ============================================================================
# НАЛАШТУВАННЯ
# ============================================================================

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
AI_VALIDATION_ENABLED = os.getenv('AI_VALIDATION_ENABLED', 'true').lower() == 'true'

# Ініціалізація OpenAI (legacy) для зображень
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    logger.warning("OPENAI_API_KEY not set - AI validation will be disabled")

# Ініціалізація нового клієнта для PDF через Responses API
_responses_client = None
if OPENAI_API_KEY and OpenAI is not None:
    _responses_client = OpenAI(api_key=OPENAI_API_KEY)

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

        # Моделі (можеш винести в env при бажанні)
        self.image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-4o")
        self.pdf_model = os.getenv("OPENAI_PDF_MODEL", "gpt-4o")  # Responses API model

        # кеш, щоб не аплоадити один і той самий pdf повторно
        self._pdf_file_id_cache: Dict[Tuple[str, int], str] = {}

    # ---------------- JSON cleaning helpers ----------------

    def _extract_json_text(self, text: str) -> str:
        """
        Витягує перший JSON-об'єкт з довільного тексту.
        Підтримує випадки:
        - ```json ... ```
        - текст до/після JSON
        - просто валідний JSON
        """
        if not text:
            raise ValueError("Empty model response")

        t = text.strip()

        # 1) прибираємо markdown code fences на початку/в кінці
        #    ```json\n{...}\n```  ->  {...}
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
            t = re.sub(r"\s*```$", "", t).strip()

        # 2) якщо вже чистий json-об'єкт
        if t.startswith("{") and t.endswith("}"):
            return t

        # 3) знаходимо перший JSON-об'єкт по балансу фігурних дужок
        start = t.find("{")
        if start == -1:
            raise ValueError("No '{' found in model response")

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(t)):
            ch = t[i]

            # коректно ігноруємо дужки всередині строк
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            else:
                if ch == '"':
                    in_string = True
                    continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return t[start:i + 1]

        raise ValueError("JSON object not closed in model response")

    def _safe_json_loads(self, text: str) -> Dict:
        """
        1) пробує json.loads(text)
        2) якщо падає — чистить текст і пробує ще раз
        """
        try:
            return json.loads(text)
        except Exception:
            cleaned = self._extract_json_text(text)
            return json.loads(cleaned)

    # ---------------- main flow ----------------

    def validate_document(self, file_path: str, document_type: str) -> Optional[ValidationResult]:
        """Перевірити документ за допомогою AI"""
        if not self.enabled:
            logger.info("AI validation is disabled")
            return None

        prompt_key = DOCUMENT_TYPE_TO_PROMPT.get(document_type)
        if prompt_key is None:
            logger.info(f"AI validation not needed for document type: {document_type}")
            return None

        try:
            prompt = DOCUMENT_PROMPTS.get(prompt_key)
            if not prompt:
                logger.error(f"Prompt not found for document type: {document_type}")
                return None

            ext = os.path.splitext(file_path.lower())[1]

            # ✅ IMAGE
            if self._is_image_file(file_path):
                base64_image = self._encode_image(file_path)
                if not base64_image:
                    logger.error(f"Failed to encode image: {file_path}")
                    return ValidationResult(
                        status='uncertain',
                        error_code='damaged_file',
                        reason='Не вдалося обробити файл'
                    )

                logger.info(f"Calling GPT-4 Vision (images) for document type: {document_type}")
                ai_response = self._call_gpt4_vision(prompt, base64_image)

                result = self._parse_ai_response(ai_response)
                logger.info(f"AI validation result: {result.status} (error_code: {result.error_code})")
                return result

            # ✅ PDF
            if ext == ".pdf":
                if _responses_client is None:
                    logger.warning("Responses client is not available (upgrade openai sdk). Skipping PDF AI validation.")
                    return None

                logger.info(f"Calling OpenAI Responses (PDF) for document type: {document_type}")
                ai_response = self._call_pdf_responses(prompt, file_path)

                result = self._parse_ai_response(ai_response)
                logger.info(f"AI validation result: {result.status} (error_code: {result.error_code})")
                return result

            # ❌ other
            logger.info(f"Unsupported file type for AI validation: {file_path}")
            return None

        except Exception as e:
            logger.error(f"Error during AI validation: {e}", exc_info=True)
            return ValidationResult(
                status='uncertain',
                error_code='uncertain',
                reason=f'Помилка AI-перевірки: {str(e)}'
            )

    def _is_image_file(self, file_path: str) -> bool:
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        _, ext = os.path.splitext(file_path.lower())
        return ext in image_extensions

    def _encode_image(self, file_path: str) -> Optional[str]:
        """Конвертувати зображення в base64"""
        try:
            with Image.open(file_path) as img:
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGB')

                max_size = (2048, 2048)
                if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    logger.info(f"Resized image to {img.size}")

                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                image_bytes = buffer.getvalue()
                return base64.b64encode(image_bytes).decode('utf-8')

        except Exception as e:
            logger.error(f"Error encoding image: {e}")
            return None

    def _call_gpt4_vision(self, prompt: str, base64_image: str) -> Dict:
        """Викликати GPT-4 Vision API (images) через Chat Completions"""
        try:
            response = openai.chat.completions.create(
                model=self.image_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=1000,
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content or ""
            logger.debug(f"GPT-4 Vision raw response: {content}")

            # ✅ безопасный парсинг (на случай если вдруг прилетит мусор)
            return self._safe_json_loads(content)

        except Exception as e:
            logger.error(f"Error calling GPT-4 Vision API: {e}")
            raise

    # ---------------- PDF via Responses API ----------------

    def _upload_pdf_get_file_id(self, pdf_path: str) -> str:
        """Upload PDF to Files API and return file_id (cached)."""
        assert _responses_client is not None

        size = os.path.getsize(pdf_path)
        key = (pdf_path, size)
        if key in self._pdf_file_id_cache:
            return self._pdf_file_id_cache[key]

        with open(pdf_path, "rb") as f:
            up = _responses_client.files.create(file=f, purpose="user_data")

        self._pdf_file_id_cache[key] = up.id
        logger.info(f"Uploaded PDF to OpenAI Files API: file_id={up.id}, size={size}")
        return up.id

    def _call_pdf_responses(self, prompt: str, pdf_path: str) -> Dict:
        """
        Викликати OpenAI Responses API для PDF:
        - PDF -> input_file (file_id)
        - + input_text prompt
        Очікуємо строгий JSON у відповіді (але чистимо, якщо модель додала ```json).
        """
        assert _responses_client is not None

        file_id = self._upload_pdf_get_file_id(pdf_path)

        # (опціонально) підсилюємо вимогу до формату
        strict_prompt = (
            "Відповідай ТІЛЬКИ валідним JSON-об’єктом. "
            "Без markdown, без ```json, без пояснень. "
            "Починай відповідь з '{' і закінчуй '}'.\n\n"
            + prompt
        )

        resp = _responses_client.responses.create(
            model=self.pdf_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": strict_prompt},
                        {"type": "input_file", "file_id": file_id},
                    ],
                }
            ],
            max_output_tokens=1000,
            temperature=0.1,
        )

        raw_text = (getattr(resp, "output_text", "") or "").strip()
        logger.info(f"OpenAI raw response text (PDF): {raw_text[:500]}{'...' if len(raw_text) > 500 else ''}")

        # ✅ чистим и парсим
        return self._safe_json_loads(raw_text)

    # ---------------- parse response ----------------

    def _parse_ai_response(self, ai_response: Dict) -> ValidationResult:
        """Парсити відповідь від AI та конвертувати в ValidationResult"""
        try:
            quality_check = ai_response.get('quality_check', 'PASSED')
            error_code = ai_response.get('error_code')
            document_check = ai_response.get('document_check', {})
            doc_status = document_check.get('status', 'UNCERTAIN')
            doc_error_code = document_check.get('error_code')

            final_error_code = doc_error_code or error_code

            if quality_check == 'FAILED':
                return ValidationResult(
                    status='rejected',
                    error_code=final_error_code or 'poor_quality',
                    reason='Документ не пройшов перевірку якості',
                    ai_response=ai_response
                )

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

            logger.warning(f"Unknown document status: {doc_status}")
            return ValidationResult(
                status='uncertain',
                error_code='uncertain',
                reason='Невідомий статус перевірки',
                ai_response=ai_response
            )

        except Exception as e:
            logger.error(f"Error parsing AI response: {e}", exc_info=True)
            return ValidationResult(
                status='uncertain',
                error_code='uncertain',
                reason=f'Помилка обробки відповіді AI: {str(e)}',
                ai_response=ai_response
            )

# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

validator = AIDocumentValidator()
