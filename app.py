import os
import tempfile
import asyncio
import uuid
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
import httpx
import json

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

async def send_text_message(phone_number: str) -> bool:
    """Send welcome text message first"""
    try:
        # Build the text message with proper formatting
        # Using \n for line breaks in WhatsApp
        text_message = """Welcome To ZOD Manpower,
Your Selected CVs"""

        payload = {
            "chatId": f"{phone_number}@c.us",
            "text": text_message
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-text",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": API_KEY
                }
            )
            
            if response.status_code == 200 or response.status_code == 201:
                print("✅ Welcome text message sent")
                return True
            else:
                print(f"❌ Text message failed: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Error sending text: {e}")
        return False

async def send_image_to_whatsapp(phone_number: str, image_url: str, index: int, total: int):
    """Send CV image only - no caption"""
    try:
        payload = {
            "chatId": f"{phone_number}@c.us",
            "url": image_url
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-image",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": API_KEY
                }
            )
            
            if response.status_code == 200 or response.status_code == 201:
                print(f"✅ CV {index}/{total} sent")
                return True
            else:
                # Try send-document as fallback
                doc_payload = {
                    "chatId": f"{phone_number}@c.us",
                    "url": image_url,
                    "filename": f"CV_{index}.jpg"
                }
                doc_response = await client.post(
                    f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-document",
                    json=doc_payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": API_KEY
                    }
                )
                
                if doc_response.status_code == 200 or doc_response.status_code == 201:
                    print(f"✅ CV {index}/{total} sent via document")
                    return True
                else:
                    print(f"❌ CV {index} failed: {doc_response.text}")
                    return False
                
    except Exception as e:
        print(f"❌ Error sending CV {index}: {e}")
        return False

async def process_cv_send(cv_ids: List[int], phoneNumber: str, task_id: str):
    try:
        progress_tasks[task_id] = {"progress": 5, "status": "Fetching CVs..."}
        selected_cvs = await fetch_cvs_from_supabase(cv_ids)
        
        if len(selected_cvs) == 0:
            progress_tasks[task_id] = {"progress": 100, "status": "No CVs found", "error": True}
            return

        total = len(selected_cvs)
        progress_tasks[task_id] = {"progress": 15, "status": f"Found {total} CVs"}

        # 🔥 Step 1: Send Welcome Text Message First
        progress_tasks[task_id] = {"progress": 20, "status": "Sending welcome message..."}
        text_sent = await send_text_message(phoneNumber)
        
        if not text_sent:
            progress_tasks[task_id] = {"progress": 25, "status": "Warning: Text message failed, continuing with images..."}
        else:
            progress_tasks[task_id] = {"progress": 25, "status": "Welcome message sent!"}

        # Step 2: Send all CV images
        success_count = 0
        for idx, cv in enumerate(selected_cvs):
            image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""

            if not image_url:
                print(f"⚠️ No image URL for CV {idx+1}")
                continue

            current_progress = 25 + ((idx + 1) / total) * 70
            progress_tasks[task_id] = {
                "progress": int(current_progress), 
                "status": f"Sending CV {idx+1}/{total}"
            }

            success = await send_image_to_whatsapp(phoneNumber, image_url, idx+1, total)
            if success:
                success_count += 1
            
            await asyncio.sleep(0.5)

        # Step 3: Complete
        if success_count == total:
            progress_tasks[task_id] = {"progress": 100, "status": f"✅ All {total} CVs sent!", "success": True}
        else:
            progress_tasks[task_id] = {"progress": 100, "status": f"⚠️ {success_count}/{total} CVs sent.", "success": True}

    except Exception as e:
        progress_tasks[task_id] = {"progress": 100, "status": f"❌ Error: {str(e)}", "error": True}
        print(f"Error: {str(e)}")

@app.post("/send-cvs", response_model=CVResponse)
async def send_cvs(request: CVRequest):
    try:
        if not request.cvIds or len(request.cvIds) == 0:
            raise HTTPException(status_code=400, detail="Please select at least one CV")

        task_id = str(uuid.uuid4())
        asyncio.create_task(process_cv_send(request.cvIds, request.phoneNumber, task_id))

        return CVResponse(
            success=True,
            message="Sending CVs...",
            task_id=task_id
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/progress/{task_id}")
async def get_progress(task_id: str):
    async def event_generator():
        last_progress = -1
        while True:
            if task_id in progress_tasks:
                data = progress_tasks[task_id]
                current_progress = data.get("progress", 0)
                
                if current_progress != last_progress:
                    last_progress = current_progress
                    yield f"data: {json.dumps(data)}\n\n"
                
                if current_progress >= 100 or data.get("error") or data.get("success"):
                    break
            else:
                yield f"data: {json.dumps({'progress': 0, 'status': 'Initializing...'})}\n\n"
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/health")
async def health_check():
    return {"status": "OK", "timestamp": "2026-08-10T19:30:10Z"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)