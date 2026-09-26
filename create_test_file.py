from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

def create_synthetic_invoice(text: str, filename: str):
    img = Image.new("RGB", (1200, 600), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 40), text, fill="black", font=font)
    path = Path("input_invoices") / filename
    img.save(path)
    print(f"Created {path}")

create_synthetic_invoice(
    "Acme Corp\n"
    "Invoice #INV-001\n"
    "Date: 2024-01-15\n"
    "Widget   1   100.00   100.00\n"
    "Total: $100.00",
    "test_invoice.png"
)
