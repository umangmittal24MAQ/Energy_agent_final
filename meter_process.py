import time
import requests
import msal
import cv2
import numpy as np
import base64
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from openai import AzureOpenAI

# ─────────────────────────────────────────────
# 1. AZURE OPENAI CONFIG
# ─────────────────────────────────────────────
load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)
DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

# ─────────────────────────────────────────────
# 2. AZURE GRAPH API CONFIG
# ─────────────────────────────────────────────
CLIENT_ID     = os.getenv("SHAREPOINT_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")
TENANT_ID     = os.getenv("SHAREPOINT_TENANT_ID")
SITE_ID       = os.getenv("SHAREPOINT_SITE_ID")
LIST_ID       = os.getenv("SHAREPOINT_LIST_ID")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
URL_BASE  = f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/lists/{LIST_ID}"

# ─────────────────────────────────────────────
# 3. OPERATING HOURS CONFIG
# ─────────────────────────────────────────────
ACTIVE_START_HOUR = 7   # 7:00 AM
ACTIVE_END_HOUR   = 20  # 8:00 PM  (20:00 in 24-hour format)

def is_within_operating_hours() -> bool:
    """Returns True if current local time is between 7:00 AM and 8:00 PM."""
    now = datetime.now()
    return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR

def seconds_until_next_window() -> int:
    """
    Calculates seconds remaining until next 7:00 AM window opens.
    Used only for informational logging — cron will actually restart the script.
    """
    now = datetime.now()
    from datetime import timedelta
    next_start = now.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0)
    if now >= next_start:
        next_start += timedelta(days=1)
    return int((next_start - now).total_seconds())

# ─────────────────────────────────────────────
# 4. OPENCV CONFIG & PREPROCESSING
# ─────────────────────────────────────────────
THRESHOLD  = 160
DILATION_W = 2
DILATION_H = 4

def _decode_base64_image(b64_str: str) -> np.ndarray:
    """Robust decoder that strips JSON wrappers and data headers."""
    cleaned_value = b64_str.strip()

    # 1. Strip JSON wrappers if present
    if cleaned_value.startswith("{"):
        try:
            file_obj = json.loads(cleaned_value)
            cleaned_value = file_obj.get("$content", file_obj.get("contentBytes", cleaned_value))
        except Exception:
            pass

    # 2. Strip Data URI headers
    if cleaned_value.startswith("data:"):
        cleaned_value = cleaned_value.split(",", 1)[1]
    if "," in cleaned_value and not cleaned_value.startswith("data:"):
        cleaned_value = cleaned_value.split(",", 1)[1]

    cleaned_value = cleaned_value.strip().strip('"').strip("'")

    # Add base64 padding
    cleaned_value += "=" * ((4 - len(cleaned_value) % 4) % 4)

    # 3. First decode
    try:
        image_bytes = base64.b64decode(cleaned_value)
    except Exception as e:
        raise ValueError(f"Initial base64 decode failed: {e}")

    # 4. Check for double-encoding (Power Automate quirk)
    if image_bytes.startswith(b"iVBORw") or image_bytes.startswith(b"/9j/"):
        image_bytes += b"=" * ((4 - len(image_bytes) % 4) % 4)
        image_bytes = base64.b64decode(image_bytes)

    # 5. Convert to OpenCV
    np_image = np.frombuffer(image_bytes, np.uint8)
    image    = cv2.imdecode(np_image, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError("cv2.imdecode returned None. Bytes are not a valid image format.")

    return image

def preprocess(image: np.ndarray) -> np.ndarray:
    """Grayscale → binary threshold → dilation."""
    gray      = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, THRESHOLD, 255, cv2.THRESH_BINARY)
    kern      = cv2.getStructuringElement(cv2.MORPH_RECT, (DILATION_W, DILATION_H))
    dilated   = cv2.dilate(binary, kern, iterations=1)
    return dilated

# ─────────────────────────────────────────────
# 5. SHAREPOINT POLLING ENGINE
# ─────────────────────────────────────────────
def get_graph_headers():
    app   = msal.ConfidentialClientApplication(CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET)
    token = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" in token:
        return {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json",
            "Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"
        }
    raise Exception("Failed to acquire Azure token.")

def process_queue():
    headers = get_graph_headers()

    # 1. Fetch Pending Items
    pending_items_url = f"{URL_BASE}/items?$expand=fields&$filter=fields/Status eq 'Pending'"
    response = requests.get(pending_items_url, headers=headers)
    response.raise_for_status()
    items = response.json().get("value", [])

    for item in items:
        item_id = item["id"]
        fields  = item.get("fields", {})
        print(f"\n[+] Processing Job ID: {item_id}")

        # Lock item to prevent duplicate processing
        requests.patch(f"{URL_BASE}/items/{item_id}/fields", headers=headers, json={"Status": "Processing"})

        # 2. Extract Base64 Strings Directly from the Column
        raw_b64_string = fields.get("ProcessedImageBase64", "")

        if not raw_b64_string:
            print(f"   [X] Job {item_id} failed: ProcessedImageBase64 column was empty.")
            requests.patch(f"{URL_BASE}/items/{item_id}/fields", headers=headers, json={"Status": "Error", "ExtractedReading": "No Image Data"})
            continue

        # Split the string back into our 3 separate images using the delimiter
        b64_images = raw_b64_string.split("|||")

        best_score    = -1
        winning_index = 0
        best_image    = None

        print(f"   [*] Found {len(b64_images)} image streams. Decoding...")

        for index, b64_str in enumerate(b64_images):
            if not b64_str.strip():
                continue

            image = _decode_base64_image(b64_str)

            if image is not None:
                processed = preprocess(image)
                score     = cv2.Laplacian(processed, cv2.CV_64F).var()

                if score > best_score:
                    best_score    = score
                    winning_index = index
                    best_image    = processed
                print(f"      -> Image {index+1} decoded successfully. Sharpness Score: {score:.2f}")
            else:
                print(f"      [!] Image {index+1} failed to decode.")

        # 3. AI Processing & Write Back
        if best_image is not None:
            print(f"   [*] Best image selected (Index {winning_index+1}). Sending to Azure OpenAI...")

            _, encoded        = cv2.imencode(".jpg", best_image)
            processed_b64     = base64.b64encode(encoded.tobytes()).decode("utf-8")

            system_prompt = """You are an expert OCR system for seven-segment LED meter displays. 
The image shows WHITE digits on a BLACK background.

The display has 4 rows. Your job:
- Read ROW 3 (second from the bottom) — it shows exactly 4 digits, NO decimal point
- Read ROW 4 (the very bottom row) — it shows exactly 4 digits WITH a decimal point

### Strict Rules:
- Count rows from the bottom: bottom = Row 4, one above = Row 3
- Row 3 will always be a whole number with exactly 4 digits (e.g. 1876)
- Row 4 will always have a decimal point (e.g. 394.0)
- Never skip leading zeros
- Never add units or extra text
- If a digit is ambiguous between 0 and 8, prefer 0 if the center is hollow, 8 if filled
- If a digit is ambiguous between 1 and 7, prefer 1 if only the right vertical bar is lit

### Output ONLY this JSON, nothing else:
{
  "row3": "<4 digits>",
  "row4": "<4 digits with decimal>",
  "combined": "<row3 joined directly with row4>"
}"""

            try:
                response = client.chat.completions.create(
                    model=DEPLOYMENT_NAME,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": "Extract the meter readings from the provided image according to the rules."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{processed_b64}", "detail": "high"}}
                        ]}
                    ]
                )

                raw_content   = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                if not raw_content:
                    print(f"   [!] Azure returned empty content. Finish Reason: {finish_reason}")
                    raise ValueError(f"Empty API response. Reason: {finish_reason}")

                print(f"   -> Raw AI Output: {raw_content}")

                ai_result         = json.loads(raw_content)
                extracted_reading = ai_result.get("combined", "Error")
                print(f"   -> AI Extracted Final: {extracted_reading}")

                update_payload = {
                    "Status": "Completed",
                    "WinningIndex": winning_index,
                    "ExtractedReading": extracted_reading
                }
                requests.patch(f"{URL_BASE}/items/{item_id}/fields", headers=headers, json=update_payload)
                print(f"[✓] Job {item_id} successfully closed out.")

            except Exception as e:
                print(f"   -> Azure OpenAI Error: {e}")
                requests.patch(f"{URL_BASE}/items/{item_id}/fields", headers=headers, json={"Status": "Error", "ExtractedReading": "AI API Failed"})
                print(f"[X] Job {item_id} failed at the AI processing step.")

# ─────────────────────────────────────────────
# 6. MAIN ENTRY POINT WITH TIME-WINDOW GUARD
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=======================================")
    print(" Energy OCR Engine Started (Azure Mode)")
    print(f" Operating Hours: {ACTIVE_START_HOUR:02d}:00 AM – {ACTIVE_END_HOUR % 12 or 12:02d}:00 PM")
    print("=======================================\n")

    # ── Safety guard: exit immediately if launched outside operating hours ──
    # This handles edge cases where cron fires slightly early, the VM clock
    # drifts, or someone triggers the script manually at the wrong time.
    if not is_within_operating_hours():
        now = datetime.now()
        secs = seconds_until_next_window()
        hrs, remainder = divmod(secs, 3600)
        mins = remainder // 60
        print(f"[!] Current time is {now.strftime('%H:%M')} — outside operating window.")
        print(f"[!] Next window opens at {ACTIVE_START_HOUR:02d}:00 AM "
              f"(in ~{hrs}h {mins}m). Exiting.")
        sys.exit(0)

    print(f"[✓] Within operating hours. Watching SharePoint List for jobs...\n")

    while True:
        # ── Per-loop time check: shut down cleanly when window closes ──
        if not is_within_operating_hours():
            now = datetime.now()
            print(f"\n[!] Time is now {now.strftime('%H:%M')} — operating window closed.")
            print(f"[!] Engine shutting down. Cron will restart at {ACTIVE_START_HOUR:02d}:00 AM tomorrow.")
            sys.exit(0)

        try:
            process_queue()
        except Exception as e:
            print(f"[!] Engine exception: {e}")

        time.sleep(3)
