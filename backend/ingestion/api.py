import uuid
import uvicorn
from fastapi import FastAPI, UploadFile, File

# Import the engine you just built!
from ocr_engine import UniversalOCREngine

app = FastAPI(title="Ingestion Pipeline API")
engine = UniversalOCREngine()

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    API Endpoint for Dev 1 (Frontend) to upload files.
    """
    # 1. Read the raw bytes from the uploaded file
    file_bytes = await file.read()
    
    # 2. Generate a unique Document ID for the database
    document_id = str(uuid.uuid4())
    
    # 3. Pass the bytes into your MarkItDown engine
    results = engine.run(document_id=document_id, file_bytes=file_bytes)
    
    # 4. Return a success response with the metadata
    return {
        "message": "File processed successfully",
        "document_id": document_id,
        "filename": file.filename,
        "extracted_pages": len(results),
        # Taking the confidence score of the first page as a quick preview
        "confidence_score": results[0].confidence if results else 0.0
    }

if __name__ == "__main__":
    # Runs the server locally on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)