import streamlit as st
from openai import OpenAI
from PIL import Image
import base64
from io import BytesIO
import smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import re
from datetime import datetime

# =====================================================================
# LOAD SECRETS
# =====================================================================
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]

# DEBUG LINE
st.write ("api KEY LOADED:", bool(st.secrets.get("OPENAI_API_KEY")))

# Convert comma-separated list → python list
TO_LIST = [x.strip() for x in TO_EMAILS.split(",") if x.strip()]
CC_LIST = [x.strip() for x in CC_EMAILS.split(",") if x.strip()]


# =====================================================================
# EMAIL SENDER
# =====================================================================
def send_email(subject, body, attachments=None, to_list=None, cc_list=None):
    if to_list is None:
        to_list = []
    if cc_list is None:
        cc_list = []

    msg = MIMEMultipart()
    msg["From"] = f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"] = ", ".join(to_list)
    msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "html"))

    if attachments:
        for filename, file_bytes in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, to_list + cc_list, msg.as_string())


# =====================================================================
# OPENAI OCR (FINAL, FIXED, WORKING)
# =====================================================================
def run_ocr(image_file):
    try:
        # FIX: extract bytes correctly
        if hasattr(image_file, "getvalue"):
            raw = image_file.getvalue()
        else:
            raw = image_file.read()

        img = Image.open(BytesIO(raw))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64_img = base64.b64encode(buf.getvalue()).decode()

        prompt = """
Extract only these fields:

TAG: (5–7 digit number)
SIZE: (format: 12.5 x 34.75)
QTY: (1 digit)
TYPE: (CLT, LOWE, CLEAR, BRONZE, LAMI, E180, Q180, i89, etc.)

FORMAT:
TAG: xxx
SIZE: xxx
QTY: xxx
TYPE: xxx
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                        }
                    ]
                }
            ],
            max_tokens=200,
        )

        txt = response.choices[0].message.content

        return {
            "tag": re.search(r"TAG:\s*(.*)", txt).group(1).strip() if re.search(r"TAG:", txt) else "OCR ERROR",
            "size": re.search(r"SIZE:\s*(.*)", txt).group(1).strip() if re.search(r"SIZE:", txt) else "OCR ERROR",
            "qty": re.search(r"QTY:\s*(.*)", txt).group(1).strip() if re.search(r"QTY:", txt) else "1",
            "type": re.search(r"TYPE:\s*(.*)", txt).group(1).strip() if re.search(r"TYPE:", txt) else "OCR ERROR",
        }

    except Exception:
        return {"tag": "OCR ERROR", "size": "OCR ERROR", "qty": "1", "type": "OCR ERROR"}



# =====================================================================
# STREAMLIT PAGE CONFIG
# =====================================================================
st.set_page_config(page_title="Reporter – DEV v6", layout="centered")

st.title("Reporter — DEV v6")
st.write("Take up to **5 photos**, review them, then submit.")


# =====================================================================
# SESSION STATE: HOLDS BATCH
# =====================================================================
if "batch" not in st.session_state:
    st.session_state.batch = []


# ==============================
# IMAGE UPLOAD (Gallery Only)
# ==============================
photo = st.file_uploader("Upload Label Photo (JPG/PNG)", type=["jpg", "jpeg", "png"])

if photo:
    img_bytes = photo.read()

    # Debugger
    st.write("DEBUG — IMAGE BYTES:", len(img_bytes))

    if len(img_bytes) < 5000:
        st.error("Image file seems empty or corrupted. Please upload again.")
        st.stop()
else:
    img_bytes = None



# =====================================================================
# DISPLAY CURRENT BATCH
# =====================================================================
st.subheader("Current Batch")

if len(st.session_state.batch) == 0:
    st.info("No photos added yet.")
else:
    for i, row in enumerate(st.session_state.batch, start=1):
        st.write(f"### {i}. {row['time']}")
        st.image(row["image"])
        st.write(f"**Reason:** {row['reason']}")
        st.write(f"**Notes:** {row['notes']}")


# =====================================================================
# CLEAR
# =====================================================================
if st.button("Clear All"):
    st.session_state.batch = []
    st.success("Batch cleared.")


# =====================================================================
# SUBMIT BATCH (EMAIL)
# =====================================================================
if st.button("Submit All Photos"):
    if len(st.session_state.batch) == 0:
        st.error("Batch is empty.")
    else:
        rows = []
        attachments = []

        for i, item in enumerate(st.session_state.batch, start=1):
            if img_bytes is None:
                st.error("No image uploaded.")
                st.stop()
            if len(img_bytes) < 5000:
                st.error("Image file seems empty or corrupted. Please upload again.")
                st.stop()
            ocr = run_ocr(item["image"])

            rows.append(f"""
<tr>
<td>{i}</td>
<td>{item['time']}</td>
<td>{item['reason']}</td>
<td>{ocr['qty']}</td>
<td>{ocr['tag']}</td>
<td>{ocr['size']}</td>
<td>{ocr['type']}</td>
<td>{item['notes']}</td>
</tr>
""")

            # Add photo attachment
            img_bytes = item["image"].getvalue()
            attachments.append((f"label_{i}.jpg", img_bytes))

        table_html = f"""
<table border="1" cellpadding="6" cellspacing="0">
<tr>
<th>No</th><th>Time</th><th>Reason</th><th>Qty</th><th>Tag</th>
<th>Size</th><th>Type</th><th>Notes</th>
</tr>
{''.join(rows)}
</table>
"""

        body = f"""
Multiple glass defects were reported:<br><br>
{table_html}
"""

        subject = f"Glass Damage Batch Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        send_email(subject, body, attachments, TO_LIST, CC_LIST)

        st.success("Batch submitted.")
        st.session_state.batch = []



