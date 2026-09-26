# InvoiceOCR Pipeline

Automated batch processing pipeline for extracting and storing invoice data from images and PDFs using Tesseract OCR and SQLite.

## 🚀 Live Dashboard
View the project dashboard here: [https://SumitNagesia123.github.io/Invoice-OCR-Pipeline/dashboard.html](https://SumitNagesia123.github.io/Invoice-OCR-Pipeline/dashboard.html)

## 📖 About This Project
The **InvoiceOCR Pipeline** is an enterprise-grade automated document processing system. In modern business, managing hundreds or thousands of physical or PDF invoices manually is a bottleneck—prone to human error, slow, and expensive to scale.

This project was engineered to solve that bottleneck. It provides a reliable, modular, and automated pipeline that transforms unstructured invoice documents (images and PDFs) into structured, queryable relational data. 

### Why this project?
- **Efficiency**: Reduces document processing time from minutes to milliseconds.
- **Accuracy**: Employs advanced image preprocessing (OpenCV) and fine-tuned heuristic parsing to maximize data extraction fidelity.
- **Robustness**: Designed with a production-first mentality, including full deduplication, path-traversal protection, and extensive test coverage (270+ tests).
- **Transparency**: Includes a real-time, interactive web dashboard to monitor pipeline health and processing logs.

## 🏗️ Project Structure
`mermaid
graph TD
    subgraph Core ["InvoiceOCR Pipeline"]
        A[run_pipeline.py] --> B[src/intake.py]
        B --> C[src/ocr.py]
        C --> D[src/parser.py]
        D --> E[src/storage.py]
        E --> F[(data/invoices.db)]
        E --> G[processed_invoices/]
        B --> H[failed_invoices/]
        C --> I[ocr_output/]
    end
    subgraph Config ["Configuration & Utils"]
        J[src/config.py] -.-> B
        J -.-> C
        J -.-> E
        K[init_db.py] --> F
    end
    subgraph UI ["Dashboard"]
        M[dashboard.html] -.-> F
    end
`

### Folder Breakdown
- data/: SQLite database storage containing processed document records.
- input_invoices/: The ingestion "drop zone" for new files.
- processed_invoices/: Archive for successfully parsed and stored files.
- ailed_invoices/: Catch-all for files that failed validation or parsing.
- ocr_output/: Intermediate storage for raw extracted text.
- src/: Core logic modules (Intake, OCR, Parser, Storage, etc.).
- 	ests/: 270+ unit, integration, and edge-case tests ensuring high-quality output.

## ⚙️ Detailed Workflow
1. **Intake**: Automatically ingests files, validates formats (PNG/JPG/PDF), sanitizes filenames, and deduplicates using SHA-256 hashes.
2. **OCR Engine**: Applies grayscale, denoising, contrast-normalization, and deskewing using OpenCV before Tesseract 5.4.0 extracts the text.
3. **Parser**: Uses an intelligent heuristic regex engine to map messy OCR output to structured fields like Invoice Number, Vendor, Date, and Line Items.
4. **Storage**: Performs server-side validation on field types/formats before securely committing data to SQLite.

## 🛠️ Setup Instructions
1. **Install Prerequisites**: Python 3.10+, [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki), and [Poppler](https://github.com/oschwartz10612/poppler-windows/releases) (for PDF support).
2. **Setup Env**: python -m venv venv, .\venv\Scripts\activate, pip install -r requirements.txt.
3. **Configure**: Copy .env.example to .env and set TESSERACT_CMD and DATABASE_PATH.
4. **Initialize**: Run python init_db.py.
5. **Run**: Place invoices in input_invoices/ and execute python run_pipeline.py.
