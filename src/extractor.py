"""
InvoiceOCR Pipeline — LLM Invoice Extractor.

Sends raw OCR text to an LLM to extract structured data matching the schema.
"""

import json
import logging
import random
from typing import Any
from anthropic import Anthropic
from src import config, schema

logger = logging.getLogger(__name__)

# NOTE: This assumes an ANTHROPIC_API_KEY is available in the .env file.
client = Anthropic()

SYSTEM_PROMPT = """
You are an expert invoice data extraction assistant.
Extract the following fields from the provided OCR text and return ONLY valid JSON.
If a field value is missing or unclear, use null. Never guess.

Fields:
- invoice_number (str)
- invoice_date (str: YYYY-MM-DD)
- vendor_name (str)
- total_amount (float)
- line_items (list of objects: description, quantity, unit_price, line_total)

Ensure the output is 100% valid JSON.
"""

def extract_invoice_data(ocr_text: str) -> dict[str, Any]:
    """
    Send OCR text to LLM and parse the response into JSON.
    Includes simple retry logic and a mock mode for benchmarking.
    """

    if config.MOCK_EXTRACTOR:
        logger.info("Mock mode enabled: returning dummy extraction data.")
        # 90% valid, 10% invalid
        mode = random.random()
        if mode < 0.90:
            # 90% Valid data with high confidence
            return {
                "invoice_number": f"MOCK-{random.randint(1000, 9999)}",
                "invoice_date": "2026-09-30",
                "vendor_name": "Mock Stationery",
                "total_amount": 2000.00,
                "currency": "INR",
                "line_items": [
                    {
                        "description": "Mock Item",
                        "quantity": 50.0,
                        "unit_price": 40.0,
                        "line_total": 2000.0,
                    }
                ],
                "confidence": "high",
            }
        elif mode < 0.95:
            # 5% Needs Review (e.g. missing total)
            return {
                "invoice_number": f"MOCK-{random.randint(1000, 9999)}",
                "invoice_date": "2026-09-30",
                "vendor_name": "Mock Stationery",
                "total_amount": None, # Missing total
                "currency": "INR",
                "line_items": [
                    {
                        "description": "Mock Item",
                        "quantity": 50.0,
                        "unit_price": 40.0,
                        "line_total": 2000.0,
                    }
                ],
                "confidence": "low",
            }
        else:
            # 5% Deliberately invalid fail (e.g. mismatched sums)
            return {
                "invoice_number": f"MOCK-{random.randint(1000, 9999)}",
                "invoice_date": "2026-09-30",
                "vendor_name": "Mock Fail",
                "total_amount": 500.0, # Mismatched sum
                "currency": "INR",
                "line_items": [
                    {
                        "description": "Mock Item",
                        "quantity": 10.0,
                        "unit_price": 40.0,
                        "line_total": 400.0,
                    }
                ],
                "confidence": "low",
            }

    prompt = f"Extract invoice data from the following text:\n\n{ocr_text}"

    for attempt in range(3):
        try:
            message = client.messages.create(
                model="anthropic/claude-3-5-sonnet-20241022",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )

            # Extract content from response
            content = message.content[0].text

            # Attempt to parse JSON
            data = json.loads(content)
            return data

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Extraction attempt {attempt+1} failed: {e}")
            if attempt == 2:
                raise e

    return {}

def process_and_validate(ocr_text: str, file_name: str, file_hash: str) -> dict[str, Any]:
    """
    Coordinates extraction and Pydantic validation.
    """
    raw_data = extract_invoice_data(ocr_text)

    # Add meta-fields required by schema
    raw_data["file_name"] = file_name
    raw_data["file_hash"] = file_hash

    # Validate against schema
    is_valid, error, validated_model = schema.validate_invoice(raw_data)

    if is_valid:
        return validated_model.model_dump()
    else:
        # Fallback: flag for review
        logger.error(f"Validation failed for {file_name}: {error}")
        raw_data["status"] = "needs_review"
        return raw_data
