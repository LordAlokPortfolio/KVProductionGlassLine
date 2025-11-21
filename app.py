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
import hashlib

# =====================================================================
# SECRETS
# =====================================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets.get("CC_EMAILS", "")
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================================
# VALID SUFFIXES
# =====================================================================
VALID_SUFFIXES = [
    "T","E180ESC","Q180","E272","Q272",
    "E366","Q366","EI89","QI89",
    "NREEDV","MATT","GRY","P621","BRZ"
]

# =====================================================================
# SESSION STATE
# =====================================================================
if "batch" not in st.session_state:
    st.session_state.batch = []

if "added_keys" not in st.session_state:
    st.session_state.added_keys = set()

if "task_queue" not in st.session_state:
    st.session_state.task_queue = []

# =====================================================================
# IMAGE PREPROCESSING
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
# OCR RAW TEXT
# =====================================================================
def ocr_raw(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    try:
        r = client.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Extract raw text only. No explanation."},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
                ]
            }]
        )
        return r.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR: {e}"

# =====================================================================
# PREFIX REPAIR
# =====================================================================
def repair_prefix(t):
    patterns = {
        r"\b3\s*9\b": "3.9", r"\b39\b": "3.9", r"\b3[-/:,_ ]+9\b": "3.9",
        r"\b3\s*1\b": "3.1", r"\b31\b": "3.1", r"\b3[-/:,_ ]+1\b": "3.1",
        r"\b4\s*7\b": "4.7", r"\b47\b": "4.7", r"\b4[-/:,_ ]+7\b": "4.7",
        r"\b5\s*7\b": "5.7", r"\b57\b": "5.7", r"\b5[-/:,_ ]+7\b": "5.7",
    }
    for pat, rep in patterns.items():
        t = re.sub(pat, rep, t)
    return t

# =====================================================================
# SUFFIX SIMILARITY
# =====================================================================
def similarity(a,b):
    a,b = a.upper(),b.upper()
    return sum(1 for x,y in zip(a,b) if x == y)

# =====================================================================
# TYPE EXTRACTION (BOLD-LINE RULE)
# =====================================================================
def extract_type(raw):
    raw = raw.replace("\r","").replace("\n"," ")
    raw = re.sub(r"\s+"," ",raw)

    # find the Cut> line
    cut_idx = None
    tokens = raw.split(" ")
    for i,t in enumerate(tokens):
        if "cut>" in t.lower():
            cut_idx = i
            break
    if cut_idx is None:
        return ""

    # reconstruct lines for more reliable parsing
    lines = re.split(r"[\n\r]+", raw)
    # fallback: re-split by two spaces
    if len(lines) <= 1:
        lines = raw.split("  ")

    # find the actual line containing "Cut>"
    cut_line_i = None
    for i,l in enumerate(lines):
        if "cut>" in l.lower():
            cut_line_i = i
            break
    if cut_line_i is None:
        return ""

    # TYPE = next non-empty line
    for j in range(cut_line_i+1, len(lines)):
        candidate = lines[j].strip()
        if candidate:
            type_line = candidate
            break
    else:
        return ""

    # clean spacing
    type_line = repair_prefix(type_line)
    type_line = re.sub(r"\s+"," ",type_line)

    # normalize CLT/CLA spacing
    type_line = re.sub(r"C\s*L\s*T", "CLT", type_line)
    type_line = re.sub(r"C\s*L\s*A", "CLA", type_line)

    # fix suffix typos
    parts = type_line.split()
    fixed_parts = []
    for p in parts:
        up = p.upper()
        best = up
        best_score = -1
        for valid in VALID_SUFFIXES:
            s = similarity(up, valid)
            if s > best_score:
                best_score = s
                best = valid
        if best_score >= 2:
            fixed_parts.append(best)
        else:
            fixed_parts.append(p)

    # final cleaned spacing
    return " ".join(fixed_parts).strip()

# =====================================================================
# SIZE EXTRACTION
# =====================================================================
def extract_size(raw):
    m = re.search(r"(\d{2,4})\s*(\d+\/\d+).*?(\d{2,4})\s*(\d+\/\d+)", raw)
    if not m: return ""
    return f"{m.group(1)} {m.group(2)} x {m.group(3)} {m.group(4)}"

# =====================================================================
# TAG EXTRACTION
# =====================================================================
def extract_tag(raw):
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""

# =====================================================================
# PO EXTRACTION
# =====================================================================
def extract_po(raw):
    m = re.findall(r"\b\d{3,6}-\d{3}\b", raw)
    return m[0] if m else ""

# =====================================================================
# EMAIL SENDER
# =====================================================================
def send_email(table_string, csv_bytes, images):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS: msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    msg.set_content("Batch submitted. Please review attachments.")

    msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="glass_report.csv")

    for i,img_bytes in enumerate(images):
        msg.add_attachment(img_bytes, maintype="image", subtype="jpeg", filename=f"label_{i+1}.jpg")

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USER,EMAIL_PASS)
        smtp.send_message(msg)

# =====================================================================
# BACKGROUND QUEUE PROCESSOR
# =====================================================================
def process_queue():
    if st.session_state.task_queue:
        task = st.session_state.task_queue.pop(0)
        send_email(task["table_string"], task["csv_bytes"], task["images"])

# =====================================================================
# UI — MULTI-PHOTO
# =====================================================================
st.title("KV Glass Damage Reporter (Multi-photo, Background Send)")

mode = st.radio("Select method:", ["Take Photo", "Upload Photos"])

if mode == "Take Photo":
    cam = st.camera_input("Capture Photo")
    if cam:
        img_bytes = cam.getvalue()
        key = hashlib.sha256(img_bytes).hexdigest()
        if key not in st.session_state.added_keys:
            st.session_state.batch.append({"img": img_bytes, "key": key, "qty": "", "reason": ""})
            st.session_state.added_keys.add(key)

if mode == "Upload Photos":
    files = st.file_uploader("Upload Photos", type=["jpg","jpeg","png"], accept_multiple_files=True)
    if files:
        for f in files:
            img_bytes = f.read()
            key = hashlib.sha256(img_bytes).hexdigest()
            if key not in st.session_state.added_keys:
                st.session_state.batch.append({"img": img_bytes, "key": key, "qty": "", "reason": ""})
                st.session_state.added_keys.add(key)

st.subheader("Photos Added")
if not st.session_state.batch:
    st.info("No photos added.")
else:
    remove = []
    for i,entry in enumerate(st.session_state.batch):
        st.write(f"### Photo {i+1}")
        st.image(entry["img"])
        entry["qty"] = st.text_input(f"Qty for Photo {i+1}", entry["qty"], key=f"qty{i}")
        entry["reason"] = st.text_input(f"Reason for Photo {i+1}", entry["reason"], key=f"reason{i}")
        if st.button(f"Remove Photo {i+1}",key=f"rm{i}"):
            remove.append(i)

    for idx in sorted(remove, reverse=True):
        st.session_state.added_keys.remove(st.session_state.batch[idx]["key"])
        del st.session_state.batch[idx]

if st.session_state.batch:
    if st.button("Send Batch"):
        rows=[]
        images=[]
        csv_buf=io.StringIO()
        writer=csv.writer(csv_buf)
        writer.writerow(["index","Size","Type","Tag#","PO#","Qty","Reason"])

        for i,entry in enumerate(st.session_state.batch, start=1):
            img=entry["img"]
            images.append(img)
            raw=ocr_raw(preprocess(img))
            size=extract_size(raw)
            gtype=extract_type(raw)
            tag=extract_tag(raw)
            po=extract_po(raw)
            qty=entry["qty"]
            reason=entry["reason"]

            writer.writerow([i,size,gtype,tag,po,qty,reason])
            rows.append([i,size,gtype,tag,po,qty,reason])

        table_string="Rows attached in CSV."
        csv_bytes=csv_buf.getvalue().encode()

        st.session_state.task_queue.append({
            "table_string":table_string,
            "csv_bytes":csv_bytes,
            "images":images
        })

        st.session_state.batch=[]
        st.session_state.added_keys=set()

        st.success("Batch submitted!")

# background send
process_queue()
