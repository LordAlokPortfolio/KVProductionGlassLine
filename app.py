import streamlit as st
import base64
import hashlib
import io
import csv
import re
from datetime import datetime
from email.message import EmailMessage
import smtplib

from openai import OpenAI
from PIL import Image, ImageEnhance, ImageOps

# ==========================================================
# LOAD SECRETS
# ==========================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets.get("CC_EMAILS", "")
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================================
# VALID SUFFIXES (tint layer)
# ==========================================================
VALID_SUFFIXES = [
    "E180", "E180ESC", "Q180", "E272", "Q272",
    "E366", "Q366", "EI89", "QI89",
    "NREEDV", "MATT", "GRY", "P621", "BRZ"
]

# ==========================================================
# SESSION STATE
# ==========================================================
if "batch" not in st.session_state:
    st.session_state.batch = []

if "added_keys" not in st.session_state:
    st.session_state.added_keys = set()

if "task_queue" not in st.session_state:
    st.session_state.task_queue = []


# ==========================================================
# IMAGE PREPROCESSING FOR BETTER OCR
# ==========================================================
def preprocess(img_bytes):
    img = Image.open(io.BytesIO(img_bytes))
    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


# ==========================================================
# OCR RAW TEXT (GPT-4O-MINI)
# ==========================================================
def ocr_raw(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    try:
        r = client.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Extract raw text only. No explanation."},
                    {"type": "input_image",
                     "image_url": f"data:image/jpeg;base64,{b64}"}
                ]
            }]
        )
        return r.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR: {e}"


# ==========================================================
# PREFIX REPAIR: 39→3.9, 47→4.7, 57→5.7, etc.
# ==========================================================
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


# ==========================================================
# SUFFIX SIMILARITY (for tint correction)
# ==========================================================
def similarity(a, b):
    a = a.upper()
    b = b.upper()
    return sum(1 for x, y in zip(a, b) if x == y)


# ==========================================================
# TYPE LINE → THICKNESS / TYPE / TINT
# ==========================================================
def extract_type_components(raw):
    raw_clean = raw.replace("\r", "").replace("\n", "\n")
    lines = raw_clean.split("\n")
    
    # find Cut> line
    cut_i = None
    for i, line in enumerate(lines):
        if "cut>" in line.lower():
            cut_i = i
            break
    if cut_i is None:
        return "", "", ""

    # TYPE is next non-empty line
    type_line = ""
    for j in range(cut_i + 1, len(lines)):
        if lines[j].strip():
            type_line = lines[j].strip()
            break

    if not type_line:
        return "", "", ""

    # clean spacing
    t = repair_prefix(type_line)
    t = re.sub(r"\s+", " ", t)

    # normalize CLT/CLA spacing
    t = re.sub(r"C\s*L\s*T", "CLT", t)
    t = re.sub(r"C\s*L\s*A", "CLA", t)

    parts = t.split()

    # thickness = first part
    thickness = parts[0] if parts else ""

    # tint = last part matching suffix dictionary
    tint = ""
    for p in parts:
        up = p.upper()
        best = up
        best_score = -1
        for suf in VALID_SUFFIXES:
            sc = similarity(up, suf)
            if sc > best_score:
                best_score = sc
                best = suf
        if best_score >= 2:
            tint = best

    # type = everything between thickness and tint
    glass_type = ""
    if thickness and tint:
        mid = t.replace(thickness, "", 1).replace(tint, "", 1).strip()
        glass_type = mid

    return thickness, glass_type, tint


# ==========================================================
# SIZE EXTRACTION (whole, fraction, mixed, inferred)
# ==========================================================
def extract_size(raw):
    text = raw.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    # 1) Fraction / Fraction
    m = re.search(r"(\d{1,4}\s*\d+\/\d+)[xX ]+(\d{1,4}\s*\d+\/\d+)", text)
    if m:
        return f"{m.group(1).strip()} x {m.group(2).strip()}"

    # 2) Fraction / Whole
    m = re.search(r"(\d{1,4}\s*\d+\/\d+)[xX ]+(\d{1,4})\b", text)
    if m:
        return f"{m.group(1).strip()} x {m.group(2).strip()}"

    # 3) Whole / Fraction
    m = re.search(r"(\d{1,4})[xX ]+(\d{1,4}\s*\d+\/\d+)", text)
    if m:
        return f"{m.group(1).strip()} x {m.group(2).strip()}"

    # 4) Whole / Whole
    m = re.search(r"(\d{1,4})\s*[xX]\s*(\d{1,4})", text)
    if m:
        return f"{m.group(1).strip()} x {m.group(2).strip()}"

    # 5) Missing "x": infer two numbers
    m = re.search(r"\b(\d{1,4})\s+(\d{1,4})\b", text)
    if m:
        return f"{m.group(1)} x {m.group(2)}"

    return ""


# ==========================================================
# TAG
# ==========================================================
def extract_tag(raw):
    m = re.search(r"\b\d{6}(?:[-.,]\d+)?\b", raw)
    return m.group(0) if m else ""


# ==========================================================
# PO
# ==========================================================
def extract_po(raw):
    m = re.findall(r"\b\d{3,6}-\d{3}\b", raw)
    return m[0] if m else ""


# ==========================================================
# EMAIL (BACKGROUND)
# ==========================================================
def send_email(csv_bytes, images):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = TO_EMAILS
    if CC_EMAILS:
        msg["Cc"] = CC_EMAILS
    msg["Subject"] = f"Glass Damage Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    msg.set_content("Batch submitted. Please review attachments.")

    msg.add_attachment(
        csv_bytes,
        maintype="text",
        subtype="csv",
        filename="glass_report.csv"
    )

    for i, img in enumerate(images):
        msg.add_attachment(
            img,
            maintype="image",
            subtype="jpeg",
            filename=f"label_{i+1}.jpg"
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)


# ==========================================================
# BACKGROUND QUEUE
# ==========================================================
def process_queue():
    if st.session_state.task_queue:
        task = st.session_state.task_queue.pop(0)
        send_email(task["csv_bytes"], task["images"])


# ==========================================================
# UI — MULTI PHOTO
# ==========================================================
st.title("KV Glass Damage Reporter (Multi-photo Background Send)")

mode = st.radio("Choose method:", ["Take Photo", "Upload Photos"])

# --- Camera
if mode == "Take Photo":
    cam = st.camera_input("Capture")
    if cam:
        img = cam.getvalue()
        key = hashlib.sha256(img).hexdigest()
        if key not in st.session_state.added_keys:
            st.session_state.batch.append({"img": img, "key": key, "qty": "", "reason": ""})
            st.session_state.added_keys.add(key)

# --- Upload
if mode == "Upload Photos":
    files = st.file_uploader("Upload", type=["jpg","jpeg","png"], accept_multiple_files=True)
    if files:
        for f in files:
            img = f.read()
            key = hashlib.sha256(img).hexdigest()
            if key not in st.session_state.added_keys:
                st.session_state.batch.append({"img": img, "key": key, "qty": "", "reason": ""})
                st.session_state.added_keys.add(key)

# ==========================================================
# SHOW BATCH
# ==========================================================
st.subheader("Photos Added")

if not st.session_state.batch:
    st.info("No photos added.")
else:
    remove_list = []

    for i, entry in enumerate(st.session_state.batch):
        st.write(f"### Photo {i+1}")
        st.image(entry["img"])

        entry["qty"] = st.text_input(f"Qty (Photo {i+1})", entry["qty"], key=f"qty{i}")

        entry["reason"] = st.radio(
            f"Reason (Photo {i+1})",
            ["Scratched", "Missing", "Broken", "KV Production Issue"],
            key=f"reason{i}"
        )

        if st.button(f"Remove Photo {i+1}", key=f"rm{i}"):
            remove_list.append(i)

    for idx in sorted(remove_list, reverse=True):
        st.session_state.added_keys.remove(st.session_state.batch[idx]["key"])
        del st.session_state.batch[idx]


# ==========================================================
# SEND BATCH
# ==========================================================
if st.session_state.batch:
    if st.button("Send Batch"):
        rows = []
        images = []
        buf = io.StringIO()
        writer = csv.writer(buf)

        writer.writerow(["index","Thickness","Type","Tint","Size","Tag#","PO#","Qty","Reason"])

        for i, entry in enumerate(st.session_state.batch, start=1):
            img_bytes = entry["img"]
            images.append(img_bytes)

            raw = ocr_raw(preprocess(img_bytes))

            thickness, glass_type, tint = extract_type_components(raw)
            size = extract_size(raw)
            tag = extract_tag(raw)
            po = extract_po(raw)
            qty = entry["qty"]
            reason = entry["reason"]

            writer.writerow([i, thickness, glass_type, tint, size, tag, po, qty, reason])

        csv_bytes = buf.getvalue().encode()

        # queue the background task
        st.session_state.task_queue.append({
            "csv_bytes": csv_bytes,
            "images": images
        })

        # clear UI
        st.session_state.batch = []
        st.session_state.added_keys = set()

        st.success("Batch submitted!")

process_queue()
