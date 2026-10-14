
import os
from PIL import Image, ImageDraw, ImageFont
import random

# Ensure folder exists
os.makedirs("input_invoices", exist_ok=True)

def create_dummy_invoice_image(filename):
    # Create simple white image
    img = Image.new('RGB', (800, 1000), color=(255, 255, 255))
    d = ImageDraw.Draw(img)

    # Add dummy text (mimics invoice layout)
    invoice_no = f"INV-{random.randint(1000, 9999)}"
    d.text((50, 50), f"ABC Stationery", fill=(0, 0, 0))
    d.text((50, 80), f"Invoice No: {invoice_no}", fill=(0, 0, 0))
    d.text((50, 110), f"Date: 2026-09-30", fill=(0, 0, 0))
    d.text((50, 150), "Notebook: 50 * 40.00 = 2000.00", fill=(0, 0, 0))
    d.text((50, 200), "Total: 2000.00", fill=(0, 0, 0))

    img.save(filename, "PNG")

print("Generating 500 dummy invoice images...")
for i in range(500):
    create_dummy_invoice_image(f"input_invoices/invoice_{i:03d}.png")
print("Finished!")
