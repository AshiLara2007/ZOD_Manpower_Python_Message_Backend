import os
import tempfile
import io
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from fpdf import FPDF

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
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://zod-cv-backend-python.fastapicloud.dev")
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ksyxmoqzcghszrhlpaxh.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_U289_qf4pkGHp-G1C4kX5w_2bztcmOg")

class CVRequest(BaseModel):
    cvIds: List[int]
    phoneNumber: str

class CVResponse(BaseModel):
    success: bool
    message: str

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

def generate_pdf_fpdf2(cv_list: List[dict]) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    for idx, cv in enumerate(cv_list):
        name = cv.get("name") or cv.get("candidate_name") or "Unnamed Candidate"
        job = cv.get("job") or cv.get("job_role") or "N/A"
        image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""

        pdf.add_page()

        if image_url:
            img_data = download_image(image_url)
            if img_data:
                try:
                    # Save image to temp file
                    temp_image_path = tempfile.mktemp(suffix=".jpg")
                    with open(temp_image_path, "wb") as f:
                        f.write(img_data)
                    # Insert image (fpdf2 can handle JPEG without Pillow)
                    pdf.image(temp_image_path, x=10, y=20, w=190)
                    os.unlink(temp_image_path)
                except Exception as e:
                    print(f"Error processing image {image_url}: {e}")
                    pdf.set_font("Helvetica", size=12)
                    pdf.cell(190, 10, "CV Image unavailable", ln=True, align='C')
            else:
                pdf.set_font("Helvetica", size=12)
                pdf.cell(190, 10, "CV Image unavailable", ln=True, align='C')
        else:
            pdf.set_font("Helvetica", size=12)
            pdf.cell(190, 10, "No CV Image URL found", ln=True, align='C')

        # Footer
        pdf.set_y(270)
        pdf.set_font("Helvetica", "B", size=14)
        pdf.cell(190, 10, name, ln=True, align='C')
        pdf.set_font("Helvetica", size=12)
        pdf.cell(190, 8, job, ln=True, align='C')
        pdf.set_font("Helvetica", size=8)
        pdf.cell(190, 6, f"Page {idx + 1} of {len(cv_list)}", ln=True, align='C')

    return pdf.output(dest='S').encode('latin1')

@app.post("/send-cvs", response_model=CVResponse)
async def send_cvs(request: CVRequest):
    try:
        print(f"Received request for: {request.phoneNumber}")
        print(f"CV IDs: {request.cvIds}")

        if not request.cvIds or len(request.cvIds) == 0:
            raise HTTPException(status_code=400, detail="Please select at least one CV")

        selected_cvs = await fetch_cvs_from_supabase(request.cvIds)

        if len(selected_cvs) == 0:
            raise HTTPException(status_code=404, detail="No valid CVs found in database")

        print(f"Generating PDF with {len(selected_cvs)} CV images...")

        pdf_bytes = generate_pdf_fpdf2(selected_cvs)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_file.write(pdf_bytes)
            temp_path = tmp_file.name

        file_url = f"{PUBLIC_URL}/serve-pdf"

        print(f"File URL: {file_url}")
        print("Sending PDF to OpenWA...")

        async with httpx.AsyncClient(timeout=300.0) as client:
            payload = {
                "chatId": f"{request.phoneNumber}@c.us",
                "url": file_url,
                "filename": "Selected CVs.pdf",
                "caption": f"Selected CVs ({len(selected_cvs)})"
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

        print(f"OpenWA Response Status: {response.status_code}")

        os.unlink(temp_path)

        return CVResponse(
            success=True,
            message=f"{len(selected_cvs)} CV(s) sent successfully!"
        )

    except httpx.HTTPStatusError as e:
        print(f"OpenWA Error: {e.response.text}")
        raise HTTPException(status_code=500, detail=f"OpenWA API error: {e.response.text}")
    except Exception as e:
        print(f"Error: {str(e)}")
        try:
            os.unlink(temp_path)
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))

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
    uvicorn.run(app, host="0.0.0.0", port=5000)