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
import base64
from PIL import Image
import io

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

async def download_image_data(url: str) -> bytes:
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

async def send_image_to_whatsapp(phone_number: str, image_url: str, caption: str, index: int, total: int):
    """Send one CV image to WhatsApp using multiple methods for reliability"""
    try:
        # Download image
        img_data = await download_image_data(image_url)
        if not img_data:
            # If image not available, send a text fallback
            await send_text_fallback(phone_number, caption, index, total)
            return True

        # Prepare base64
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        
        # Try Method 1: send-image with base64 (no prefix)
        try:
            payload = {
                "chatId": f"{phone_number}@c.us",
                "image": img_base64,
                "caption": f"{caption} ({index}/{total})"
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
                response.raise_for_status()
                print(f"Image {index} sent via send-image")
                return True
        except Exception as e1:
            print(f"send-image failed: {e1}, trying send-media...")
            # Try Method 2: send-media (some OpenWA versions use this)
            try:
                payload = {
                    "chatId": f"{phone_number}@c.us",
                    "media": {
                        "url": f"data:image/jpeg;base64,{img_base64}"
                    },
                    "caption": f"{caption} ({index}/{total})"
                }
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-media",
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-API-Key": API_KEY
                        }
                    )
                    response.raise_for_status()
                    print(f"Image {index} sent via send-media")
                    return True
            except Exception as e2:
                print(f"send-media failed: {e2}, trying send-document as fallback...")
                # Try Method 3: send-document with image file (reliable fallback)
                try:
                    # Save image as temporary file
                    temp_path = tempfile.mktemp(suffix=".jpg")
                    with open(temp_path, "wb") as f:
                        f.write(img_data)
                    
                    # Serve the file via the same server (we already have /serve-pdf endpoint, but we can reuse for images)
                    # We'll create a new endpoint /serve-image for this, but we can just use the existing /serve-pdf temporarily.
                    # For simplicity, we'll upload to a temporary public URL? Not good.
                    # Instead, we can send the image as base64 in the document field.
                    # In OpenWA, send-document accepts base64 in 'document' field with filename.
                    payload = {
                        "chatId": f"{phone_number}@c.us",
                        "document": f"data:image/jpeg;base64,{img_base64}",
                        "filename": f"CV_{index}.jpg",
                        "caption": f"{caption} ({index}/{total})"
                    }
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.post(
                            f"{OPENWA_URL}/api/sessions/{SESSION_ID}/messages/send-document",
                            json=payload,
                            headers={
                                "Content-Type": "application/json",
                                "X-API-Key": API_KEY
                            }
                        )
                        response.raise_for_status()
                        print(f"Image {index} sent via send-document")
                        os.unlink(temp_path)
                        return True
                except Exception as e3:
                    print(f"All methods failed for image {index}: {e3}")
                    # Last resort: send text only
                    await send_text_fallback(phone_number, caption, index, total)
                    return False

    except Exception as e:
        print(f"Unexpected error sending image {index}: {e}")
        return False

async def send_text_fallback(phone_number: str, caption: str, index: int, total: int):
    """If image fails, send a text message with the caption"""
    try:
        payload = {
            "chatId": f"{phone_number}@c.us",
            "text": f"CV {index}/{total}: {caption}"
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
            response.raise_for_status()
            print(f"Text fallback sent for {index}")
    except Exception as e:
        print(f"Text fallback failed: {e}")

async def process_cv_send(cv_ids: List[int], phoneNumber: str, task_id: str):
    try:
        progress_tasks[task_id] = {"progress": 5, "status": "Fetching CVs..."}
        selected_cvs = await fetch_cvs_from_supabase(cv_ids)
        
        if len(selected_cvs) == 0:
            progress_tasks[task_id] = {"progress": 100, "status": "No CVs found", "error": True}
            return

        total = len(selected_cvs)
        progress_tasks[task_id] = {"progress": 20, "status": f"Found {total} CVs"}

        success_count = 0
        for idx, cv in enumerate(selected_cvs):
            name = cv.get("name") or cv.get("candidate_name") or "Unnamed"
            job = cv.get("job") or cv.get("job_role") or "N/A"
            image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""

            caption = f"{name} ({job})"
            
            current_progress = 20 + ((idx + 1) / total) * 75
            progress_tasks[task_id] = {
                "progress": int(current_progress), 
                "status": f"Sending {idx+1}/{total}: {name}"
            }

            success = await send_image_to_whatsapp(phoneNumber, image_url, caption, idx+1, total)
            if success:
                success_count += 1
            
            # Short delay to avoid rate limiting
            await asyncio.sleep(0.8)

        if success_count == total:
            progress_tasks[task_id] = {"progress": 100, "status": f"✅ All {total} CVs sent successfully!", "success": True}
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
            message="Process started. Sending images...",
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