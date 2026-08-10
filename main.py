import os
import tempfile
from typing import List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
from pyppeteer import launch

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
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://your-app.fastapicloud.dev")
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

async def generate_pdf(cv_list: List[dict]) -> bytes:
    pages_html = ""

    for idx, cv in enumerate(cv_list):
        image_url = cv.get("cv") or cv.get("cv_url") or cv.get("cvUrl") or ""
        name = cv.get("name") or cv.get("candidate_name") or "Unnamed Candidate"
        job = cv.get("job") or cv.get("job_role") or "N/A"

        img_tag = f'<img src="{image_url}" style="max-width:100%; max-height:100%; object-fit:contain;" />' if image_url else '<div style="color:#999; font-size:20px; padding:40px;">No CV Image</div>'

        pages_html += f"""
        <div style="page-break-after:always; width:100%; height:100vh; display:flex; flex-direction:column; justify-content:center; align-items:center; background:white; padding:20px;">
            <div style="flex:1; display:flex; justify-content:center; align-items:center; width:100%; height:90%;">
                {img_tag}
            </div>
            <div style="text-align:center; font-family:Arial; margin-top:10px; padding:10px; border-top:1px solid #eee; width:100%;">
                <div style="font-weight:bold; font-size:16px;">{name}</div>
                <div style="color:#666; font-size:14px;">{job}</div>
                <div style="color:#999; font-size:12px; margin-top:4px;">Page {idx + 1} of {len(cv_list)}</div>
            </div>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Selected CVs</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ background:white; }}
            img {{ display:block; max-width:100%; max-height:90vh; object-fit:contain; }}
        </style>
    </head>
    <body>
        {pages_html}
    </body>
    </html>
    """

    browser = await launch(headless=True, args=['--no-sandbox'])
    page = await browser.newPage()
    await page.setContent(html_content, waitUntil='networkidle0')
    pdf_bytes = await page.pdf(
        format='A4',
        printBackground=True,
        margin={'top': '5mm', 'right': '5mm', 'bottom': '5mm', 'left': '5mm'}
    )
    await browser.close()
    return pdf_bytes

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

        pdf_bytes = await generate_pdf(selected_cvs)

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