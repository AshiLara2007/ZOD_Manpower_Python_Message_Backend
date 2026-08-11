import os
import tempfile
import asyncio
import uuid
from typing import List, Dict
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import httpx
from fpdf import FPDF
import json
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENWA_URL = os.getenv("OPENWA_URL", "http://localhost:2785")
SESSION_ID = os.getenv("SESSION_ID", "319f57c3-fb2f-48ee-bb92-bcdfef491fe8")
API_KEY = os.getenv("API_KEY", "owa_k1_f272efc10df6fc3e786a149044169a0809631b9f06342b10e8adcce902b1c109")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://zod-cv-backend.onrender.com")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ksyxmoqzcghszrhlpaxh.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_U289_qf4pkGHp-G1C4kX5w_2bztcmOg")

# 🔥 Progress Tracking Storage
progress_tasks: Dict[str, Dict] = {}

class CVRequest(BaseModel):
    cvIds: List[int]
    phoneNumber: str

class CVResponse(BaseModel):
    success: bool
    message: str
    task_id: str = None

async def fetch_cvs_from_supabase(cv_ids: List[int]) -> List[dict]:
    ids_param = ",".join(str(id) for id in cv_ids)
    url = f"{SUPABASE_URL}/rest/v1/talents?id=in.({ids_param})&select=*"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}"
            }
        )
        response.raise_for_status()
        return response.json()

async def download_image(url: str) -> bytes:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
    except Exception as e:
        print(f"Failed to download image: {url}, error: {e}")
        return None

async def generate_pdf(cv_list: List[dict], task_id: str) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=False)

    total = len(cv_list)
    for idx, cv in enumerate(cv_list):
        name = cv.get("name") or cv.get("candidate_name") or "Unnamed Candidate"
        job = cv.get("job") or cv.get("job_role") or "N/A"
        image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""

        pdf.add_page()

        if image_url:
            img_data = await download_image(image_url)
            if img_data:
                try:
                    temp_path = tempfile.mktemp(suffix=".jpg")
                    with open(temp_path, "wb") as f:
                        f.write(img_data)
                    
                    margin = 10
                    page_width = 210 - (2 * margin)
                    page_height = 297 - (2 * margin) - 20
                    
                    pdf.image(temp_path, x=margin, y=margin, w=page_width, h=page_height)
                    os.unlink(temp_path)
                except Exception as e:
                    print(f"Error processing image: {e}")
                    pdf.set_font("Helvetica", size=12)
                    pdf.cell(190, 10, "CV Image unavailable", ln=True, align='C')
            else:
                pdf.set_font("Helvetica", size=12)
                pdf.cell(190, 10, "CV Image unavailable", ln=True, align='C')
        else:
            pdf.set_font("Helvetica", size=12)
            pdf.cell(190, 10, "No CV Image found", ln=True, align='C')

        pdf.set_y(280)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(190, 8, name, ln=True, align='C')
        pdf.set_font("Helvetica", size=10)
        pdf.cell(190, 6, job, ln=True, align='C')
        pdf.set_font("Helvetica", size=8)
        pdf.cell(190, 5, f"Page {idx + 1} of {total}", ln=True, align='C')

        # 🔥 Progress Update: PDF Generation Progress (50%)
        progress = 30 + ((idx + 1) / total) * 20
        progress_tasks[task_id] = {"progress": int(progress), "status": f"Generating PDF {idx+1}/{total}"}

    return bytes(pdf.output(dest='S'))

async def process_cv_send(cv_ids: List[int], phoneNumber: str, task_id: str):
    try:
        # 🔥 Step 1: Fetching CVs (0-20%)
        progress_tasks[task_id] = {"progress": 5, "status": "Fetching CVs from database..."}
        await asyncio.sleep(0.5)

        selected_cvs = await fetch_cvs_from_supabase(cv_ids)
        
        if len(selected_cvs) == 0:
            progress_tasks[task_id] = {"progress": 100, "status": "No CVs found", "error": True}
            return

        progress_tasks[task_id] = {"progress": 20, "status": f"Found {len(selected_cvs)} CVs"}

        # 🔥 Step 2: Generating PDF (20-50%)
        progress_tasks[task_id] = {"progress": 25, "status": "Generating PDF..."}
        
        pdf_bytes = await generate_pdf(selected_cvs, task_id)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(pdf_bytes)
            temp_path = tmp_file.name

        progress_tasks[task_id] = {"progress": 50, "status": "PDF generated successfully"}

        file_url = f"{PUBLIC_URL}/serve-pdf"

        # 🔥 Step 3: Sending to WhatsApp (50-90%)
        progress_tasks[task_id] = {"progress": 55, "status": "Connecting to WhatsApp..."}
        await asyncio.sleep(0.5)

        caption_text = """Hey, Thanks for Selected ZOD Manpower Recruitment,
This is your selected CVs"""

        async with httpx.AsyncClient(timeout=300.0) as client:
            # Prepare and send
            progress_tasks[task_id] = {"progress": 70, "status": "Uploading PDF to WhatsApp..."}
            
            payload = {
                "chatId": f"{phoneNumber}@c.us",
                "url": file_url,
                "filename": "Selected CVs.pdf",
                "caption": caption_text
            }

            response = await client.post(
                f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-document",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": API_KEY
                }
            )
            response.raise_for_status()

        progress_tasks[task_id] = {"progress": 90, "status": "Message sent to WhatsApp"}

        os.unlink(temp_path)

        # 🔥 Step 4: Complete (100%)
        progress_tasks[task_id] = {"progress": 100, "status": "✅ Successfully sent!", "success": True}

    except Exception as e:
        progress_tasks[task_id] = {"progress": 100, "status": f"❌ Error: {str(e)}", "error": True}
        print(f"Error: {str(e)}")

@app.post("/send-cvs", response_model=CVResponse)
async def send_cvs(request: CVRequest):
    try:
        if not request.cvIds or len(request.cvIds) == 0:
            raise HTTPException(status_code=400, detail="Please select at least one CV")

        # 🔥 Generate Unique Task ID
        task_id = str(uuid.uuid4())
        
        # 🔥 Start Background Task
        asyncio.create_task(process_cv_send(request.cvIds, request.phoneNumber, task_id))

        return CVResponse(
            success=True,
            message="Process started. Check progress using task ID.",
            task_id=task_id
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/progress/{task_id}")
async def get_progress(task_id: str):
    """🔥 Live Progress Stream using Server-Sent Events (SSE)"""
    async def event_generator():
        last_progress = -1
        while True:
            if task_id in progress_tasks:
                data = progress_tasks[task_id]
                current_progress = data.get("progress", 0)
                
                # Send update only if progress changed
                if current_progress != last_progress:
                    last_progress = current_progress
                    yield f"data: {json.dumps(data)}\n\n"
                
                # If completed or error, stop streaming
                if current_progress >= 100 or data.get("error") or data.get("success"):
                    break
            else:
                # Task not found
                yield f"data: {json.dumps({'progress': 0, 'status': 'Initializing...'})}\n\n"
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/serve-pdf")
async def serve_pdf():
    temp_dir = tempfile.gettempdir()
    files = [f for f in os.listdir(temp_dir) if f.endswith(".pdf")]
    if files:
        latest_file = max(files, key=lambda f: os.path.getctime(os.path.join(temp_dir, f)))
        file_path = os.path.join(temp_dir, latest_file)
        return FileResponse(file_path, media_type="application/pdf", filename="Selected CVs.pdf")
    raise HTTPException(status_code=404, detail="PDF not found")

@app.get("/health")
async def health_check():
    return {"status": "OK", "timestamp": "2026-08-10T19:30:10Z"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)