import streamlit as st
import base64
from datetime import datetime
from email.message import EmailMessage
import smtplib
from openai import OpenAI
from PIL import Image, ImageEnhance, ImageOps
import io
import re
import csv

# =====================================================================
# LOAD SECRETS
# =====================================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets.get("CC_EMAILS", "")
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# VALID SUFFIXES (USED ONLY FOR FIXING OCR SUFFIX TYPOS)
# =====================================================================
VALID_SUFFIXES = [
    "T", "E180ESC", "Q180", "E272", "Q272",
    "E366", "Q366", "EI89", "QI89",
    "NREEDV", "MATT", "GRY", "P621", "BRZ"
]

# =====================================================================
# INITIALIZE BATCH STORAGE
# =====================================================================
if "batch" not in st.session_state:
    st.session_state.batch = []   # Each item: {img, qty, reason, notes}

# =====================================================================
# IMAGE PREPROCESSING (improves OCR accuracy)
# =====================================================================
def preprocess(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()

# =====================================================================
# OCR RAW TEXT WITH GPT-4O-MINI
# =====================================================================
def ocr_raw(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    try:
        r = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Extract raw text only. No explanations."},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
                    ]
                }
            ]
        )
        return r.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR: {e}"

# =====================================================================
# PREFIX REPAIR (fixes 39 → 3.9, 3 9 → 3.9, 3-9 → 3.9, etc.)
# =====================================================================
def repair_prefix(text):
    patterns = {
        r"\b3\s*9\b": "3.9",
        r"\b39\b": "3.9",
        r"\b3[-/:,_ ]+9\b": "3.9",

        r"\b3\s*1\b": "3.1",
        r"\b31\b": "3.1",
        r"\b3[-/:,_ ]+1\b": "3.1",

        r"\b4\s*7\b": "4.7",
        r"\b47\b": "4.7",
        r"\b4[-/:,_ ]+7\b": "4.7",

        r"\b5\s*7\b": "5.7",
        r"\b57\b": "5.7",
        r"\b5[-/:,_ ]+7\b": "5.7",
    }
    for pat, rep in patterns.items():
        text = re.sub(pat, rep, text)
    return text

# =====================================================================
# SUFFIX SIMILARITY
# =====================================================================
def similarity(a, b):
    a, b = a.upper(), b.upper()
    return sum(1 for x, y in zip(a, b) if x == y)

# =====================================================================
# TYPE EXTRACTION (EXACT ORDER PRESERVED)
# =====================================================================
def extract_type(raw):
    raw = raw.replace("\n", " ")
    raw = repair_prefix(raw)
    raw = re.sub(r"\s+", " ", raw)

    raw = re.sub(r"C\s*L\s*T", "CLT", raw)  # Normalize CLT spacing

    prefix_pat = r"(3\.1|3\.9|4\.7|5\.7)[^\n]+"
    m = re.search(prefix_pat, raw)
    if not m:
        return ""

    line = m.group(0).strip()

    parts = line.split()
    if len(parts) == 1:
        return parts[0]

    corrected = []
    for p in parts:
        raw_p = p.upper()

        best = raw_p
        best_score = -1

        for suf in VALID_SUFFIXES:
            s = similarity(raw_p, suf)
            if s > best_score:
                best_score = s
                best = suf

        if best_score >= 2:
            corrected.append(best)
        else:
            corrected.append(p)

    return " ".join(corrected).strip()

# =====================================================================
# SIZE EXTRACTION
# =====================================================================
def extract_size(raw):
    pat = r"(\d{2,4})\s*(\d+\/\d+).*?(\d{2,4})\s*(\d+\/\d+)"
    m = re.search(pat, raw)
    if not m:
        return ""
    w, wf, h, hf = m.groups()
    return f"{w} {wf} x {h} {hf}"

# =====================================================================
# TAG EXTRACTION (NO RECONSTRUCTION)
# =====================================================================
def extract_tag(raw):
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""

# =====================================================================
# PO EXTRACTION
# =====================================================================
def extract_po(raw):
    matches = re.findall(r"\b\d{3,6}-\d{3}\b", raw)
    return matches[0] if matches else ""

# =====================================================================
# EMAIL SENDER
# =====================================================================
def send_email(table_string, csv_bytes, image_list):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    msg.set_content(table_string)

    msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="glass_report.csv")

    for i, img_bytes in enumerate(image_list):
        msg.add_attachment(img_bytes, maintype="image", subtype="jpeg", filename=f"label_{i+1}.jpg")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# =====================================================================
# UI — SINGLE PAGE MULTI-PHOTO BATCH
# =====================================================================
st.title("KV Glass Damage Reporter (Multi-Photo)")

mode = st.radio("Choose Method:", ["Take Photo", "Upload Photo"])

if mode == "Take Photo":
    cam = st.camera_input("Capture Photo")
    if cam:
        st.session_state.batch.append({
            "img": cam.getvalue(),
            "qty": "",
            "reason": ""
        })

if mode == "Upload Photo":
    files = st.file_uploader("Upload Photos", type=["jpg","jpeg","png"], accept_multiple_files=True)
    if files:
        for f in files:
            st.session_state.batch.append({
                "img": f.read(),
                "qty": "",
                "reason": ""
            })

# =====================================================================
# SHOW BATCH
# =====================================================================
st.subheader("Photos Added")

if len(st.session_state.batch) == 0:
    st.info("No photos added yet.")
else:
    remove_indices = []

    for i, entry in enumerate(st.session_state.batch):
        st.write(f"### Photo {i+1}")
        st.image(entry["img"], use_column_width=True)

        entry["qty"] = st.text_input(f"Qty for Photo {i+1}", value=entry["qty"] or "", key=f"qty{i}")
        entry["reason"] = st.text_input(f"Reason for Photo {i+1}", value=entry["reason"] or "", key=f"reason{i}")

        if st.button(f"Remove Photo {i+1}", key=f"remove{i}"):
            remove_indices.append(i)

    for idx in sorted(remove_indices, reverse=True):
        del st.session_state.batch[idx]

# =====================================================================
# PROCESS AND SEND ALL
# =====================================================================
if len(st.session_state.batch) > 0:
    if st.button("Process & Send All"):
        with st.spinner("Processing all photos…"):

            rows = []
            csv_buffer = io.StringIO()
            csv_writer = csv.writer(csv_buffer)

            csv_writer.writerow(["index", "Size", "Type", "Tag#", "PO#", "Qty", "Reason"])

            images = []

            for i, entry in enumerate(st.session_state.batch, start=1):
                img_bytes = entry["img"]
                images.append(img_bytes)

                raw = ocr_raw(preprocess(img_bytes))

                size = extract_size(raw)
                gtype = extract_type(raw)
                tag = extract_tag(raw)
                po = extract_po(raw)
                qty = entry["qty"]
                reason = entry["reason"]

                rows.append(f"{i} | {size} | {gtype} | {tag} | {po} | {qty} | {reason}")
                csv_writer.writerow([i, size, gtype, tag, po, qty, reason])

            table_string = "index | Size | Type | Tag# | PO# | Qty | Reason\n" + "\n".join(rows)

            csv_bytes = csv_buffer.getvalue().encode()

            send_email(table_string, csv_bytes, images)

            st.success("All photos processed & email sent!")

            st.session_state.batch = []
