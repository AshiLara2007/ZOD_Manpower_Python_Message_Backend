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

def create_collage(image_data_list: List[bytes], names: List[str]) -> bytes:
    """Create a vertical collage of CV images with names below each image"""
    if not image_data_list:
        return None
    
    images = []
    for img_data in image_data_list:
        try:
            img = Image.open(io.BytesIO(img_data))
            # Resize to a common width (600px) to make collage look uniform
            img.thumbnail((600, 800))
            images.append(img)
        except Exception as e:
            print(f"Error processing image for collage: {e}")
            continue
    
    if not images:
        return None
    
    # Calculate collage dimensions
    total_height = sum(img.height for img in images) + (20 * len(images)) + 20
    max_width = max(img.width for img in images) + 40
    
    collage = Image.new('RGB', (max_width, total_height), color=(255, 255, 255))
    
    y_offset = 10
    for idx, img in enumerate(images):
        x_offset = (max_width - img.width) // 2
        collage.paste(img, (x_offset, y_offset))
        y_offset += img.height + 20
    
    # Save to bytes
    buffer = io.BytesIO()
    collage.save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()

async def send_collage_to_whatsapp(phone_number: str, collage_data: bytes, count: int):
    """Send the collage image as a single image message"""
    try:
        # Save collage as temp file
        temp_path = tempfile.mktemp(suffix=".jpg")
        with open(temp_path, "wb") as f:
            f.write(collage_data)
        
        # Option 1: Send as image URL (if we can serve it)
        # For simplicity, we'll use the image URL directly from the server
        # But we need to serve it via /serve-image endpoint.
        # For now, we'll just use base64 (but OpenWA may not support it).
        # Instead, we'll use the /serve-image endpoint.
        
        # Better approach: Serve the image via our own endpoint
        # We'll save the image and serve it via /serve-image
        # But we need to handle it differently.
        # For simplicity, we'll send as document (which supports base64)
        
        # Actually, we'll just send as a file URL using the server.
        # We'll save the collage and serve it via the /serve-image endpoint.
        # For now, use the /serve-image endpoint.
        
        # For this to work, we need to add a /serve-image endpoint.
        # We'll pass the file path and let it be served.
        # Let's use the existing /serve-image endpoint.
        
        # Since we have the file saved, we can use the /serve-image endpoint.
        # But we need to know the public URL for it.
        # We'll use the PUBLIC_URL + "/serve-image"
        
        # Let's save the file with a unique name
        filename = f"collage_{uuid.uuid4()}.jpg"
        file_path = os.path.join(tempfile.gettempdir(), filename)
        with open(file_path, "wb") as f:
            f.write(collage_data)
        
        # Serve via /serve-image endpoint
        file_url = f"{PUBLIC_URL}/serve-image/{filename}"
        
        payload = {
            "chatId": f"{phone_number}@c.us",
            "url": file_url,
            "caption": f"Selected CVs ({count})"
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
                print(f"✅ Collage sent successfully")
                return True
            else:
                print(f"❌ Collage failed: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Error sending collage: {e}")
        return False

@app.get("/serve-image/{filename}")
async def serve_image(filename: str):
    """Serve a temporary image file"""
    file_path = os.path.join(tempfile.gettempdir(), filename)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Image not found")

async def process_cv_send(cv_ids: List[int], phoneNumber: str, task_id: str):
    try:
        progress_tasks[task_id] = {"progress": 5, "status": "Fetching CVs..."}
        selected_cvs = await fetch_cvs_from_supabase(cv_ids)
        
        if len(selected_cvs) == 0:
            progress_tasks[task_id] = {"progress": 100, "status": "No CVs found", "error": True}
            return

        total = len(selected_cvs)
        progress_tasks[task_id] = {"progress": 20, "status": f"Found {total} CVs"}

        # Download all images
        progress_tasks[task_id] = {"progress": 30, "status": "Downloading images..."}
        image_data_list = []
        names = []
        for cv in selected_cvs:
            image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""
            if image_url:
                img_data = await download_image_data(image_url)
                if img_data:
                    image_data_list.append(img_data)
                    name = cv.get("name") or cv.get("candidate_name") or "Unnamed"
                    names.append(name)
        
        if not image_data_list:
            progress_tasks[task_id] = {"progress": 100, "status": "No images found", "error": True}
            return

        progress_tasks[task_id] = {"progress": 50, "status": "Creating collage..."}
        
        # Create collage
        collage_data = create_collage(image_data_list, names)
        if not collage_data:
            progress_tasks[task_id] = {"progress": 100, "status": "Failed to create collage", "error": True}
            return

        progress_tasks[task_id] = {"progress": 70, "status": f"Sending collage with {len(image_data_list)} CVs..."}
        
        # Send collage as single image
        success = await send_collage_to_whatsapp(phoneNumber, collage_data, len(image_data_list))
        
        if success:
            progress_tasks[task_id] = {"progress": 100, "status": f"✅ All {len(image_data_list)} CVs sent in one collage!", "success": True}
        else:
            progress_tasks[task_id] = {"progress": 100, "status": "❌ Failed to send collage", "error": True}

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
            message="Process started. Creating collage...",
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