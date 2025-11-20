import streamlit as st
import easyocr
import re
from email.message import EmailMessage
import smtplib
from datetime import datetime
from PIL import Image
import io

# ==============================
# CONFIG / SECRETS
# ==============================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]

ADMIN_PIN = st.secrets["ADMIN_PIN"]

# ==============================
# OCR INITIALIZATION (Load Once)
# ==============================
@st.cache_resource
def load_ocr():
    return easyocr.Reader(['en'], gpu=False)

reader = load_ocr()

# ==============================
# EMAIL FUNCTION
# ==============================
def send_email(subject, body, image_bytes):
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = st.secrets["TO_EMAILS"]
    msg["Cc"] = st.secrets["CC_EMAILS"]
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        image_bytes,
        maintype="image",
        subtype="jpeg",
        filename="glass_label.jpg"
    )

    with smtplib.SMTP_SSL("smtp.mail.me.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# ==============================
# OCR EXTRACTION LOGIC
# ==============================
def extract_info(text):
    text_upper = text.upper()

    # ---- TAG# extraction ----
    tag_match = re.findall(r"([A-Z]{1,3}-)?(\d{4,6})", text_upper)
    tag_num = tag_match[0][1] if tag_match else "Not Found"

    # ---- SIZE extraction ----
    size_patterns = [
        r"\d+\s*\d*\/\d*\s*X\s*\d+\s*\d*\/\d*",  # fractional inches
        r"\d{2,4}\s*X\s*\d{2,4}"                 # mm size
    ]
    sizes = []
    for p in size_patterns:
        found = re.findall(p, text_upper)
        for s in found:
            if s not in sizes:
                sizes.append(s)

    size_clean = ", ".join(sizes) if sizes else "Not Found"

    # ---- QTY extraction ----
    qty_match = re.search(r"QTY[: ]+(\d+)", text_upper)
    qty = qty_match.group(1) if qty_match else "Not Found"

    # ---- GLASS TYPE extraction ----
    GLASS_TOKENS = [
        "3.9 CLT", "CLT", "E180", "E185", "Q180", "Q185",
        "LOWE", "LOW E", "LOE", "I89",
        "LAMI", "LAMINATED",
        "CLEAR", "BRONZE"
    ]

    types_found = []
    for token in GLASS_TOKENS:
        if token in text_upper:
            types_found.append(token)

    types_clean = ", ".join(list(dict.fromkeys(types_found))) if types_found else "Not Found"

    return tag_num, size_clean, qty, types_clean

# ==============================
# UI SETTINGS (iPhone PWA compatible)
# ==============================
st.markdown("""
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
""", unsafe_allow_html=True)

# ==============================
# SIDEBAR
# ==============================
menu = st.sidebar.radio("Menu", ["Submit Report", "Admin Panel"])

# ==============================
# ADMIN PANEL
# ==============================
if menu == "Admin Panel":
    st.title("Admin Panel")

    pin = st.text_input("Enter Admin PIN", type="password")
    if pin != ADMIN_PIN:
        st.warning("Incorrect PIN")
        st.stop()

    st.success("Admin Access Granted")

    st.write("""
    • OCR Version Active  
    • EasyOCR Enabled  
    • Everything logged through vendor email threads  
    """)

    st.stop()

# ==============================
# SUBMISSION PAGE
# ==============================
st.title("KV Glass Damage Reporter (OCR Enabled)")
photo = st.camera_input("Take Photo of Label")

# Dropdowns
reason_list = ["Scratched", "Broken", "Missing", "Wrong Size", "Wrong Type", "Other"]
reason = st.selectbox("Reason", reason_list)

note = st.text_input("Additional Notes (if Other)") if reason == "Other" else "None"

dept_map = {"PD": "Patio Door", "WD": "Window", "ED": "Entry Door", "SU": "Service Unit"}
dept_key = st.selectbox("Department", list(dept_map.keys()))

# ==============================
# SUBMIT BUTTON
# ==============================
if st.button("Submit Report"):
    if not photo:
        st.error("Please take a photo first.")
        st.stop()

    img_bytes = photo.getvalue()
    img = Image.open(io.BytesIO(img_bytes))

    # ---- OCR ----
    ocr_result = reader.readtext(img_bytes, detail=0, paragraph=True)
    ocr_text = " ".join(ocr_result)

    # ---- Extraction ----
    tag_num, size_clean, qty, types_clean = extract_info(ocr_text)

    # ---- Timestamp ----
    ref_id = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ---- Email Content ----
    subject = f"Glass Damage Report – {dept_key} – {reason} – Ref {ref_id}"

    body = f"""
A glass has been found to be defective and is submitted by Production.

Extracted Label Details:
• Tag#: {tag_num}
• Size: {size_clean}
• Qty Needed: {qty}
• Glass Type: {types_clean}

Manager Inputs:
• Department: {dept_key} ({dept_map[dept_key]})
• Reason: {reason}
• Additional Notes: {note}

Photo of the label is attached.

Please review and forward to the glass vendor for the replacement and provide approximate ETA.

Reference ID: {ref_id}

Regards,
KV Production – Glass Line
"""

    try:
        send_email(subject, body, img_bytes)
        st.success("Report Sent Successfully.")
    except Exception as e:
        st.error(f"Email failed: {e}")
