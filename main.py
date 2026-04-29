from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import os
import csv
import io
import re
from openpyxl import load_workbook

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TABBLY_API_KEY = os.getenv("API_KEY")
TABBLY_ORG_ID = os.getenv("ORG_ID")

AGENT_CAMPAIGN_MAP = {
    5537: {
        "campaign_id": 2291,
        "agent_name": "Stilwater Diabetes Care"
    },
    5485: {
        "campaign_id": 2290,
        "agent_name": "Amar Eye Yoga"
    }
}

FALLBACK_AGENTS = [
    {"id": 5537, "agent_name": "Stilwater Diabetes Care"},
    {"id": 5485, "agent_name": "Amar Eye Yoga"}
]

BATCH_SIZE = 25
TABBLY_TIMEOUT = 120

class CallRequest(BaseModel):
    phone: str
    name: str
    instruction: str
    agent_id: int

def get_campaign_id_for_agent(agent_id: int) -> int:
    config = AGENT_CAMPAIGN_MAP.get(int(agent_id))
    if not config:
        raise HTTPException(
            status_code=400,
            detail=f"No campaign configured for agent_id {agent_id}"
        )
    return int(config["campaign_id"])

def get_custom_first_line(agent_id: int, name: str) -> str:
    clean_name = str(name).strip()

    if int(agent_id) == 5537:
        return f"Hello {clean_name} I am calling from Stilwater, Please tell me more about your diabetes condition."

    if int(agent_id) == 5485:
        return f"Hello {clean_name} I am calling from Amar Eye, Please tell me more about your eye condition."

    return f"Hello {clean_name}, please tell me more about your condition."

def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()

def clean_phone(value):
    if value is None:
        return ""

    s = str(value).strip()

    if s.lower() == "none":
        return ""

    if s.endswith(".0"):
        s = s[:-2]

    s = s.replace(" ", "")
    s = s.replace("-", "")
    s = s.replace("(", "")
    s = s.replace(")", "")

    if s.startswith("+"):
        s = "+" + re.sub(r"[^\d]", "", s[1:])
    else:
        s = re.sub(r"[^\d]", "", s)

    return s

def normalize_key(key):
    if key is None:
        return ""
    k = str(key).strip().lower()
    k = k.replace("_", " ").replace("-", " ")
    k = " ".join(k.split())
    return k

def normalize_row(row):
    normalized = {}

    for key, value in row.items():
        normalized[normalize_key(key)] = value

    phone = (
        normalized.get("phone numbers")
        or normalized.get("phone number")
        or normalized.get("phone")
        or normalized.get("mobile")
        or normalized.get("mobile number")
        or normalized.get("contact number")
        or normalized.get("contact")
    )

    name = (
        normalized.get("name")
        or normalized.get("customer name")
        or normalized.get("full name")
    )

    instruction = (
        normalized.get("custom instruction")
        or normalized.get("instruction")
        or normalized.get("custom text")
        or normalized.get("notes")
    )

    phone = clean_phone(phone)
    name = clean_text(name)
    instruction = clean_text(instruction)

    return phone, name, instruction

def build_contact(phone, name, instruction, agent_id):
    campaign_id = get_campaign_id_for_agent(agent_id)
    custom_first_line = get_custom_first_line(agent_id, name)

    return {
        "phone_number": phone,
        "campaign_id": campaign_id,
        "participant_identity": name,
        "use_agent_id": int(agent_id),
        "creator_by": "api",
        "custom_first_line": custom_first_line,
        "custom_instruction": instruction,
        "sip_call_id": "NA"
    }

def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/agents")
def get_agents():
    if not TABBLY_API_KEY:
        return {
            "status": "fallback",
            "message": "API_KEY is missing, using fallback agents",
            "data": FALLBACK_AGENTS
        }

    url = "https://www.tabbly.io/api/get-agents"

    try:
        response = requests.post(
            url,
            json={"api_key": TABBLY_API_KEY},
            timeout=30
        )
    except Exception as e:
        return {
            "status": "fallback",
            "message": f"Agent API request failed: {str(e)}",
            "data": FALLBACK_AGENTS
        }

    try:
        result = response.json()
    except Exception:
        return {
            "status": "fallback",
            "message": f"Agent API returned non-JSON response: {response.text}",
            "data": FALLBACK_AGENTS
        }

    if isinstance(result, dict) and isinstance(result.get("data"), list) and len(result["data"]) > 0:
        return result

    if isinstance(result, list) and len(result) > 0:
        return {
            "status": "success",
            "data": result
        }

    return {
        "status": "fallback",
        "message": "No agents returned by API, using fallback agents",
        "original_response": result,
        "data": FALLBACK_AGENTS
    }

@app.post("/call")
def make_call(data: CallRequest):
    if not TABBLY_API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is missing")

    phone = clean_phone(data.phone)
    name = clean_text(data.name)
    instruction = clean_text(data.instruction)

    if not phone or not name or not instruction:
        raise HTTPException(status_code=400, detail="Phone, name, and instruction are required")

    campaign_id = get_campaign_id_for_agent(data.agent_id)
    custom_first_line = get_custom_first_line(data.agent_id, name)

    url = "https://www.tabbly.io/dashboard/agents/endpoints/add-campaign-contacts"

    payload = {
        "api_key": TABBLY_API_KEY,
        "contacts": [
            {
                "phone_number": phone,
                "campaign_id": campaign_id,
                "participant_identity": name,
                "use_agent_id": int(data.agent_id),
                "creator_by": "api",
                "custom_first_line": custom_first_line,
                "custom_instruction": instruction,
                "sip_call_id": "NA"
            }
        ]
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        result = response.json()
    except requests.exceptions.ReadTimeout:
        raise HTTPException(status_code=504, detail="Tabbly request timed out while adding single contact")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=result)

    return {
        "message": "Single contact added successfully",
        "selected_agent_id": int(data.agent_id),
        "mapped_campaign_id": campaign_id,
        "custom_first_line_used": custom_first_line,
        "tabbly_response": result
    }

@app.post("/bulk-upload")
async def bulk_upload(agent_id: int = Query(...), file: UploadFile = File(...)):
    if not TABBLY_API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is missing")

    campaign_id = get_campaign_id_for_agent(agent_id)

    filename = (file.filename or "").lower()
    content = await file.read()
    rows = []

    if filename.endswith(".csv"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV file must be UTF-8 encoded")

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

    elif filename.endswith(".xlsx"):
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        sheet = workbook.active
        data = list(sheet.values)

        if not data:
            raise HTTPException(status_code=400, detail="Excel file is empty")

        headers = [str(h).strip() if h is not None else "" for h in data[0]]

        for values in data[1:]:
            row = dict(zip(headers, values))
            rows.append(row)
    else:
        raise HTTPException(status_code=400, detail="Only CSV and XLSX files are supported")

    contacts = []
    skipped = []

    for idx, row in enumerate(rows, start=2):
        phone, name, instruction = normalize_row(row)

        if not phone or not name or not instruction:
            skipped.append({
                "row": idx,
                "reason": "Missing or invalid phone / name / custom instruction",
                "data": row
            })
            continue

        contacts.append(build_contact(phone, name, instruction, agent_id))

    if not contacts:
        raise HTTPException(status_code=400, detail={
            "message": "No valid contacts found in file",
            "skipped": skipped
        })

    url = "https://www.tabbly.io/dashboard/agents/endpoints/add-campaign-contacts"

    batch_results = []
    total_success = 0
    total_failed = 0

    for batch_no, batch in enumerate(chunk_list(contacts, BATCH_SIZE), start=1):
        payload = {
            "api_key": TABBLY_API_KEY,
            "contacts": batch
        }

        try:
            response = requests.post(url, json=payload, timeout=TABBLY_TIMEOUT)
            result = response.json()
        except requests.exceptions.ReadTimeout:
            batch_results.append({
                "batch_no": batch_no,
                "status": "timeout",
                "batch_size": len(batch)
            })
            total_failed += len(batch)
            continue
        except Exception as e:
            batch_results.append({
                "batch_no": batch_no,
                "status": "error",
                "batch_size": len(batch),
                "error": str(e)
            })
            total_failed += len(batch)
            continue

        if response.status_code >= 400:
            batch_results.append({
                "batch_no": batch_no,
                "status": "failed",
                "batch_size": len(batch),
                "response": result
            })
            total_failed += len(batch)
        else:
            batch_results.append({
                "batch_no": batch_no,
                "status": "success",
                "batch_size": len(batch),
                "response": result
            })

            if isinstance(result, dict) and "summary" in result:
                total_success += result["summary"].get("success", 0)
                total_failed += result["summary"].get("failed", 0)
            else:
                total_success += len(batch)

    return {
        "message": "Bulk upload processed in batches",
        "selected_agent_id": int(agent_id),
        "mapped_campaign_id": campaign_id,
        "valid_contacts": len(contacts),
        "skipped_rows": skipped,
        "batch_size": BATCH_SIZE,
        "total_success": total_success,
        "total_failed": total_failed,
        "batch_results": batch_results,
        "sample_custom_first_line": get_custom_first_line(agent_id, "Sample User")
    }

@app.get("/call-logs")
def get_logs(agent_id: int = Query(None)):
    if not TABBLY_API_KEY or not TABBLY_ORG_ID:
        raise HTTPException(status_code=500, detail="API_KEY or ORG_ID is missing")

    url = "https://www.tabbly.io/dashboard/agents/endpoints/call-logs-v2"

    params = {
        "api_key": TABBLY_API_KEY,
        "organization_id": TABBLY_ORG_ID,
        "limit": 50,
        "offset": 0
    }

    if agent_id is not None:
        campaign_id = get_campaign_id_for_agent(agent_id)
        params["campaign_id"] = str(campaign_id)

    response = requests.get(url, params=params, timeout=30)

    try:
        result = response.json()
    except Exception:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=result)

    return result