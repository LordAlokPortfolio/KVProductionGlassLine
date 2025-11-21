import streamlit as st
import io
from PIL import Image
from datetime import datetime
from email.message import EmailMessage
import smtplib
import base64
from openai import OpenAI

# ==========================================================
# LOAD SECRETS (DEV MODE → ONLY EMAIL YOU AND NING)
# ==========================================================

OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]

TO_EMAILS = st.secrets["TO_EMAILS"]        # only YOU
CC_EMAILS = st.secrets["CC_EMAILS"]        # you + ning

# ==========================================================
# OPENAI CLIENT
# ==========================================================
client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================================
# OCR FUNCTION (OpenAI gpt-4o-mini vision)
# ==========================================================
def run_ocr(image_bytes):
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        prompt_text = """
You are reading a glass manufacturing label.

Extract ONLY:
• Tag Number (digits only, no PD/WD/ED/SU)
• Size
• Glass Type
• Qty on Label

Return as JSON with keys: tag, size, type, qty
Do NOT add commentary.
"""

        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {"role": "user", "content": prompt_text},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{b64}"
                        }
                    ],
                },
            ],
        )

        raw = response.output_text

        # Extract JSON safely
        import json
        try:
            data = json.loads(raw)
            return (
                data.get("tag", "NOT FOUND"),
                data.get("size", "NOT FOUND"),
                data.get("type", "NOT FOUND"),
                data.get("qty", "NOT FOUND"),
            )
        except:
            return ("NOT FOUND", "NOT FOUND", "NOT FOUND", "NOT FOUND")

    except Exception as e:
        return ("OCR ERROR", "OCR ERROR", "OCR ERROR", "OCR ERROR")


# ==========================================================
# EMAIL FUNCTION
# ==========================================================
def send_email(subject, body, attachments):
    msg = EmailMessage()
    msg["From"] = EMAIL_USER
    msg["To"] = TO_EMAILS
    msg["Cc"] = CC_EMAILS
    msg["Subject"] = subject
    msg.set_content(body)

    for idx, img_bytes in enumerate(attachments, start=1):
        msg.add_attachment(
            img_bytes,
            maintype="image",
            subtype="jpeg",
            filename=f"label_{idx}.jpg"
        )

    with smtplib.SMTP_SSL("smtp.mail.me.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)


# ==========================================================
# SESSION STATE SETUP
# ==========================================================
if "batch" not in st.session_state:
    st.session_state.batch = []   # list of dicts with photo + details


# ==========================================================
# UI TITLE
# ==========================================================
st.title("KV Glass Reporter – DEV v5")


# ==========================================================
# TAKE PHOTO SECTION
# ==========================================================
st.subheader("1. Take Photo")

photo = st.camera_input("Take a photo of the glass label")

if photo:
    st.image(photo, caption="Captured Label", use_column_width=True)

    # Per-photo inputs
    st.subheader("2. Enter Details")

    reason = st.selectbox(
        "Reason",
        ["Scratched", "KV Production Issue", "Broken", "Missing"]
    )

    qty = st.number_input("Qty Needed", min_value=1, max_value=10, value=1)

    notes = st.text_input("Notes (optional)")

    if st.button("Add to Batch"):
        st.session_state.batch.append(
            {
                "img_bytes": photo.getvalue(),
                "reason": reason,
                "qty": qty,
                "notes": notes,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        )
        st.success("Added to batch.")
        st.rerun()


# ==========================================================
# BATCH PREVIEW SECTION
# ==========================================================
st.subheader("3. Items in Batch")

if len(st.session_state.batch) == 0:
    st.info("No photos added yet.")

else:
    for i, item in enumerate(st.session_state.batch, start=1):
        st.markdown(f"### Photo {i}")
        st.image(item["img_bytes"], width=250)
        st.write(f"• **Reason:** {item['reason']}")
        st.write(f"• **Qty:** {item['qty']}")
        st.write(f"• **Notes:** {item['notes'] or '—'}")
        st.write(f"• **Time:** {item['time']}")

        if st.button(f"Remove Photo {i}", key=f"remove_{i}"):
            st.session_state.batch.pop(i - 1)
            st.rerun()


# ==========================================================
# SUBMIT BATCH
# ==========================================================
if len(st.session_state.batch) > 0:
    st.subheader("4. Submit")

    if st.button("Submit Batch (Email)"):
        rows = []
        attachments = []

        for item in st.session_state.batch:
            img_bytes = item["img_bytes"]
            reason = item["reason"]
            qty = item["qty"]
            notes = item["notes"]
            time = item["time"]

            # OCR
            tag, size, gtype, ocr_qty = run_ocr(img_bytes)

            rows.append(
                f"{len(rows)+1:<3} {time:<20} {reason:<20} {qty:<4} {tag:<10} {size:<20} {gtype:<20} {notes}"
            )

            attachments.append(img_bytes)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        subject = f"Glass Damage Batch Report – {timestamp}"

        header = (
            "No  Time                 Reason               Qty  Tag        Size                 Type                 Notes\n"
            "---------------------------------------------------------------------------------------------------------------"
        )

        body = header + "\n" + "\n".join(rows)

        try:
            send_email(subject, body, attachments)
            st.success("Batch sent successfully.")
            st.session_state.batch = []
        except Exception as e:
            st.error(f"Email failed: {e}")

