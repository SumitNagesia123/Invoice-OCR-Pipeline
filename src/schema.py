"""
InvoiceOCR Pipeline — Pydantic schema and validation rules.

Defines the structured format for invoice data and enforces
data integrity via Pydantic.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import date

class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None

    @model_validator(mode='after')
    def validate_line_item(self) -> 'LineItem':
        if self.quantity is not None and self.unit_price is not None and self.line_total is None:
            # Basic business check if line_total is missing but components exist
            self.line_total = round(self.quantity * self.unit_price, 2)
        return self

class InvoiceSchema(BaseModel):
    invoice_number: str = Field(..., min_length=1)
    invoice_date: date
    vendor_name: str = Field(..., min_length=1)
    customer_name: Optional[str] = None
    currency: str = "USD"

    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total_amount: float = Field(..., gt=0)

    line_items: List[LineItem] = []

    file_name: str
    file_hash: str
    status: str = "needs_review"

    @model_validator(mode='after')
    def validate_totals(self) -> 'InvoiceSchema':
        # Business logic checks
        # 1. Check if subtotal matches sum of line totals (if available)
        if self.subtotal is not None and self.line_items:
            expected_subtotal = sum((item.line_total or 0) for item in self.line_items)
            if abs(self.subtotal - expected_subtotal) > 0.05:
                self.status = "needs_review"

        # 2. Check if subtotal + tax = total
        if self.subtotal is not None and self.tax is not None:
            if abs((self.subtotal + self.tax) - self.total_amount) > 0.05:
                self.status = "needs_review"

        return self

def validate_invoice(data: dict) -> tuple[bool, str | None, InvoiceSchema | None]:
    """
    Validate raw dict data against InvoiceSchema.
    Returns (is_valid, error_message, validated_model).
    """
    try:
        validated = InvoiceSchema(**data)
        return True, None, validated
    except Exception as e:
        return False, str(e), None
