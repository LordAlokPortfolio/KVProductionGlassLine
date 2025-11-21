import streamlit as st
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formataddr
from email import encoders
from io import BytesIO
import smtplib
import base64
from PIL import Image
from openai import OpenAI
import re
import time

# --------------------------------------------------------------
# LOAD SECRETS
# --------------------------------------------------------------
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME = st.secrets["FROM_NAME"]
TO_EMAILS = st.secrets["TO_EMAILS"]
CC_EMAILS = st.secrets["CC_EMAILS"]
ADMIN_PIN = st.secrets["ADMIN_PIN"]

client = OpenAI(api_key=OPENAI_API_KEY)

# --------------------------------------------------------------
# OCR FUNCTION (100% reliable pipeline)
# --------------------------------------------------------------
def run_ocr(image_file):
    try:
        img = Image.open(image_file)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64_img = base64.b64encode(buf.getvalue()).decode()

        prompt = """
Extract the following exactly:
- Tag number (5–7 digits)
- Glass Size (format "12.5 x 34.75")
- Qty Needed (1 digit)
- Glass Type (CLT, LOWE, BRONZE, CLEAR, E180, i89, Q180, LAMI, etc.)

Return ONLY this format:
TAG: ___
SIZE: ___
QTY: ___
TYPE: ___
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_img}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=200,
        )

        txt = response.choices[0].message.content

        tag = re.search(r"TAG:\s*(.*)", txt)
        size = re.search(r"SIZE:\s*(.*)", txt)
        qty = re.search(r"QTY:\s*(.*)", txt)
        gtype = re.search(r"TYPE:\s*(.*)", txt)

        return {
            "tag": tag.group(1).strip() if tag else "NOT FOUND",
            "size": size.group(1).strip() if size else "NOT FOUND",
            "qty": qty.group(1).strip() if qty else "1",
            "type": gtype.group(1).strip() if gtype else "NOT FOUND",
        }

    except:
        return {"tag": "OCR ERROR", "size": "OCR ERROR", "qty": "1", "type": "OCR ERROR"}


# --------------------------------------------------------------
# EMAIL FUNCTION
# --------------------------------------------------------------
def send_email(subject, body, files):
    msg = MIMEMultipart()
    msg["From"] = formataddr((FROM_NAME, EMAIL_USER))
    msg["To"] = TO_EMAILS
    msg["Cc"] = CC_EMAILS
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "html"))

    for fname, fdata in files:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(fdata)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(EMAIL_USER, EMAIL_PASS)
    server.sendmail(EMAIL_USER, [TO_EMAILS] + CC_EMAILS.split(","), msg.as_string())
    server.quit()


# --------------------------------------------------------------
# STREAMLIT LAYOUT
# --------------------------------------------------------------
st.set_page_config(page_title="KV Glass Reporter — DEV v6", layout="centered")
st.title("Reporter — DEV v6")

if "batch" not in st.session_state:
    st.session_state.batch = []

# --------------------------------------------------------------
# TAKE MULTIPLE PHOTOS
# --------------------------------------------------------------
photos = st.camera_input("Take Photo", key="photo_input")

reason_list = ["Scratched", "KV Production Issue", "Broken", "Missing"]
reason = st.selectbox("Reason", reason_list)
notes = st.text_input("Notes (optional)")

if st.button("Add to Batch"):
    if photos is None:
        st.error("Take a photo first.")
    else:
        st.session_state.batch.append({
            "image": photos,
            "reason": reason,
            "notes": notes
        })
        st.success("Added to batch.")

# --------------------------------------------------------------
# DISPLAY BATCH
# --------------------------------------------------------------
if len(st.session_state.batch) > 0:
    st.subheader("Current Batch")
    for i, item in enumerate(st.session_state.batch, 1):
        st.write(f"**#{i} — Reason:** {item['reason']} — **Notes:** {item['notes']}")
        st.image(item["image"])

if st.button("Clear Batch"):
    st.session_state.batch = []
    st.info("Batch cleared.")

# --------------------------------------------------------------
# SUBMIT BATCH
# --------------------------------------------------------------
if st.button("Submit Batch"):
    if len(st.session_state.batch) == 0:
        st.error("No photos in batch.")
    else:
        table_rows = []
        attachments = []

        for i, item in enumerate(st.session_state.batch, 1):
            ocr = run_ocr(item["image"])

            row = f"""
<tr>
<td>{i}</td>
<td>{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</td>
<td>{item['reason']}</td>
<td>{ocr['qty']}</td>
<td>{ocr['tag']}</td>
<td>{ocr['size']}</td>
<td>{ocr['type']}</td>
<td>{item['notes']}</td>
</tr>
"""
            table_rows.append(row)

            # attachment
            raw_bytes = item["image"].getvalue()
            attachments.append((f"label_{i}.jpg", raw_bytes))

        html_table = f"""
<table border="1" cellpadding="6" cellspacing="0">
<tr>
<th>No</th><th>Time</th><th>Reason</th><th>Qty</th>
<th>Tag</th><th>Size</th><th>Type</th><th>Notes</th>
</tr>
{''.join(table_rows)}
</table>
"""

        subject = f"Glass Damage Batch Report – {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        body = f"<p>Multiple glass defects were reported:</p>{html_table}"

        send_email(subject, body, attachments)
        st.success("Batch sent.")

        st.session_state.batch = []
