import streamlit as st
import requests
import base64
import io
from PIL import Image
from datetime import datetime, time as dtime
import csv
import smtplib
from email.message import EmailMessage

# =========================================================
# LOAD SECRETS (dev branch → ONLY YOU receive emails)
# =========================================================
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
YOUR_EMAIL = st.secrets["YOUR_EMAIL"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]

# =========================================================
# EMAIL SENDER (SMTP)
# =========================================================
def send_email(subject, body, attachments=None, to=None):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if attachments:
        for filename, data in attachments.items():
            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=filename
            )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# =========================================================
# OPENAI OCR FUNCTION
# =========================================================
def ocr_with_openai(image_bytes):
    b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": "gpt-4o-mini",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Extract strictly from the label:\n"
                            "• TAG number\n"
                            "• SIZE\n"
                            "• QTY\n"
                            "• GLASS TYPE\n"
                            "Return clean values without extra commentary."
                        )
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}"
                    }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            json=payload,
            headers=headers,
            timeout=40
        )
        text = resp.json()["output_text"]
        return parse_ocr_text(text)
    except:
        return {
            "tag": "NOT FOUND",
            "size": "NOT FOUND",
            "qty": "NOT FOUND",
            "glass": "NOT FOUND"
        }

# Simple extractor
def parse_ocr_text(text):
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    data = {"tag": "NOT FOUND", "size": "NOT FOUND", "qty": "NOT FOUND", "glass": "NOT FOUND"}

    for ln in lines:
        u = ln.upper()
        if "TAG" in u or "LABEL" in u:
            data["tag"] = ln.split(":")[-1].strip()
        elif "SIZE" in u:
            data["size"] = ln.split(":")[-1].strip()
        elif "QTY" in u or "QUANTITY" in u:
            data["qty"] = ln.split(":")[-1].strip()
        elif "TYPE" in u or "GLASS" in u or "SPEC" in u:
            data["glass"] = ln.split(":")[-1].strip()

    return data

# =========================================================
# DEV VOLATILE MEMORY (RAM ONLY)
# =========================================================
if "photos" not in st.session_state:
    st.session_state["photos"] = []

if "records" not in st.session_state:
    st.session_state["records"] = []

if "last_export" not in st.session_state:
    st.session_state["last_export"] = None

# =========================================================
# AUTO EXPORT LOGIC
# =========================================================
EXPORT_TIMES = [
    dtime(9,0),
    dtime(12,0),
    dtime(15,0),
    dtime(18,0),
    dtime(21,0)
]

SATURDAY_EXPORT = [
    dtime(9,0),
    dtime(12,0)
]

def is_export_time(now):
    wd = now.weekday()
    now_clean = now.time().replace(second=0, microsecond=0)

    if wd == 5:  # Saturday
        return now_clean in SATURDAY_EXPORT

    if wd in [0,1,2,3,4]:  # Weekdays
        return now_clean in EXPORT_TIMES

    return False  # Sunday

def auto_export_check():
    now = datetime.now()
    if not is_export_time(now):
        return

    current_slot = now.strftime("%H:%M")

    # Prevent resend
    if st.session_state["last_export"] == current_slot:
        return

    # If records exist → export
    if len(st.session_state["records"]) > 0:
        export_records()

    st.session_state["records"] = []
    st.session_state["last_export"] = current_slot

def export_records():
    rows = st.session_state["records"]
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["Timestamp", "Tag", "Size", "Qty", "Glass"])

    for row in rows:
        writer.writerow(row)

    csv_bytes = csv_buf.getvalue().encode()

    send_email(
        subject="KV Glass Auto Export (DEV)",
        body="Attached is the scheduled export.",
        attachments={"export.csv": csv_bytes},
        to=YOUR_EMAIL
    )

# =========================================================
# UI — CLEAN + PHONE OPTIMIZED
# =========================================================
st.title("KV Glass Damage Reporter — DEV v3")

st.write("Take up to **5 photos**, then press **Submit All Photos**.")

# CAMERA
photo = st.camera_input("Take Photo", key="cam")

if photo:
    if len(st.session_state["photos"]) < 5:
        st.session_state["photos"].append(photo.getvalue())
        st.success(f"Photo added ({len(st.session_state['photos'])}/5).")
    else:
        st.error("Maximum 5 photos reached.")

if st.button("Retake / Clear All Photos"):
    st.session_state["photos"] = []
    st.experimental_rerun()

# =========================================================
# SUBMIT ALL PHOTOS
# =========================================================
if st.button("Submit All Photos"):
    if len(st.session_state["photos"]) == 0:
        st.error("No photos taken.")
        st.stop()

    ocr_results = []
    email_attachments = {}

    with st.spinner("Processing all photos..."):
        for i, img_bytes in enumerate(st.session_state["photos"]):
            ocr = ocr_with_openai(img_bytes)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

            ocr_results.append((timestamp, ocr["tag"], ocr["size"], ocr["qty"], ocr["glass"]))

            # Save to dev RAM
            st.session_state["records"].append([
                timestamp,
                ocr["tag"],
                ocr["size"],
                ocr["qty"],
                ocr["glass"]
            ])

            email_attachments[f"label_{i+1}.jpg"] = img_bytes

    # Build email body
    body_lines = ["Multiple glass defects were reported:\n"]
    for r in ocr_results:
        ts, tag, size, qty, glass = r
        body_lines.append(
            f"• {ts} — TAG:{tag}, Size:{size}, Qty:{qty}, Glass:{glass}"
        )

    email_body = "\n".join(body_lines)

    send_email(
        subject="Glass Damage Report (DEV Batch)",
        body=email_body,
        attachments=email_attachments,
        to=YOUR_EMAIL
    )

    st.success("Submitted. Email sent to you (DEV MODE).")
    st.session_state["photos"] = []

# =========================================================
# AUTO-EXPORT ENGINE
# =========================================================
auto_export_check()

# =========================================================
# ADMIN PANEL
# =========================================================
st.sidebar.title("Admin")
pin = st.sidebar.text_input("Enter PIN", type="password")

if pin == ADMIN_PIN:
    st.sidebar.success("Admin Mode Active")
    st.sidebar.write("Records in memory:", len(st.session_state["records"]))
    if st.sidebar.button("Force Export Now"):
        if len(st.session_state["records"]) == 0:
            st.sidebar.warning("No records to export.")
        else:
            export_records()
            st.sidebar.success("Manual export completed.")
else:
    st.sidebar.info("Enter PIN to access admin panel.")
# =========================================================