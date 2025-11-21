import streamlit as st
import base64, hashlib, io, csv, re, smtplib
from datetime import datetime
from email.message import EmailMessage
from openai import OpenAI
from PIL import Image, ImageEnhance, ImageOps

# ==========================================================
# SECRETS
# ==========================================================
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASS = st.secrets["EMAIL_PASS"]
FROM_NAME  = st.secrets["FROM_NAME"]
TO_EMAILS  = st.secrets["TO_EMAILS"]
CC_EMAILS  = st.secrets.get("CC_EMAILS","")
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================================
# CONSTANTS
# ==========================================================
VALID_SUFFIXES = [
    "E180","E180ESC","Q180","E272","Q272",
    "E366","Q366","EI89","QI89",
    "NREEDV","MATT","GRY","P621","BRZ"
]

SIZE_REJECT_PATTERNS = [
    r"\b20\d{2}\b",                 # years 2000-2099
    r"\d{1,2}:\d{1,2}",             # colon codes 29:1:44
    r"WO:", r"WD-"
]

# ==========================================================
# SESSION
# ==========================================================
if "batch" not in st.session_state: st.session_state.batch=[]
if "added_keys" not in st.session_state: st.session_state.added_keys=set()
if "task_queue" not in st.session_state: st.session_state.task_queue=[]

# ==========================================================
# IMAGE PREPROCESS
# ==========================================================
def preprocess(img_bytes):
    img=Image.open(io.BytesIO(img_bytes))
    img=ImageOps.grayscale(img)
    img=ImageEnhance.Contrast(img).enhance(2.0)
    img=ImageEnhance.Sharpness(img).enhance(1.5)
    buf=io.BytesIO(); img.save(buf,format="JPEG",quality=92)
    return buf.getvalue()

# ==========================================================
# OCR
# ==========================================================
def ocr_raw(img_bytes):
    b64=base64.b64encode(img_bytes).decode()
    try:
        r=client.responses.create(
            model="gpt-4o-mini",
            input=[{
                "role":"user",
                "content":[
                    {"type":"input_text","text":"Extract raw text only."},
                    {"type":"input_image","image_url":f"data:image/jpeg;base64,{b64}"}
                ]
            }]
        )
        return r.output_text.strip()
    except Exception as e:
        return f"OCR_ERROR:{e}"

# ==========================================================
# PREFIX REPAIR
# ==========================================================
def repair_prefix(t):
    patterns={
        r"\b3\s*9\b":"3.9",r"\b39\b":"3.9",r"\b3[-/:,_ ]+9\b":"3.9",
        r"\b3\s*1\b":"3.1",r"\b31\b":"3.1",r"\b3[-/:,_ ]+1\b":"3.1",
        r"\b4\s*7\b":"4.7",r"\b47\b":"4.7",r"\b4[-/:,_ ]+7\b":"4.7",
        r"\b5\s*7\b":"5.7",r"\b57\b":"5.7",r"\b5[-/:,_ ]+7\b":"5.7"
    }
    for pat,rep in patterns.items(): t=re.sub(pat,rep,t)
    return t

def similarity(a,b): return sum(1 for x,y in zip(a.upper(),b.upper()) if x==y)

# ==========================================================
# TYPE LINE → THICKNESS / TYPE / TINT
# ==========================================================
def extract_type_components(raw):
    lines=[l.strip() for l in raw.split("\n") if l.strip()]

    # find Cut> line
    ci=None
    for i,l in enumerate(lines):
        if "cut>" in l.lower():
            ci=i; break
    if ci is None: return "","",""

    # next non-empty line
    if ci+1>=len(lines): return "","",""
    tline=repair_prefix(lines[ci+1])
    tline=re.sub(r"\s+"," ",tline)
    tline=re.sub(r"C\s*L\s*T","CLT",tline)
    tline=re.sub(r"C\s*L\s*A","CLA",tline)

    parts=tline.split()
    if not parts: return "","",""

    thickness=parts[0]
    tint=""; glass_type=""

    for p in parts:
        up=p.upper()
        best=up; best_score=-1
        for suf in VALID_SUFFIXES:
            sc=similarity(up,suf)
            if sc>best_score: best_score=sc; best=suf
        if best_score>=2: tint=best

    if tint:
        mid = tline.replace(thickness,"",1).replace(tint,"",1).strip()
        glass_type = re.sub(r"\s+"," ",mid)

    return thickness, glass_type, tint

# ==========================================================
# SIZE EXTRACTION (FIRST VALID AFTER TYPE)
# ==========================================================
def is_valid_size_line(l):
    if l.count("x")==0 and l.count("X")==0: return False
    for pat in SIZE_REJECT_PATTERNS:
        if re.search(pat,l): return False
    if ":" in l: return False
    return True

def clean_size(s):
    s=re.sub(r"[^0-9xX/ ]"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    s=s.replace("X","x")
    # keep only W x H pattern
    m=re.search(r"(\d{1,4}(?:\s*\d+/\d+)?)[ ]*x[ ]*(\d{1,4}(?:\s*\d+/\d+)?)",s)
    if m: return f"{m.group(1).strip()} x {m.group(2).strip()}"
    return ""

def extract_size(raw):
    lines=[l.strip() for l in raw.split("\n") if l.strip()]

    # find Cut> line
    ci=None
    for i,l in enumerate(lines):
        if "cut>" in l.lower():
            ci=i;break
    if ci is None: return ""

    # scan lines after type line for valid size
    for j in range(ci+2,len(lines)):
        l=lines[j]
        if is_valid_size_line(l):
            out=clean_size(l)
            if out: return out

    # fallback: infer two numbers
    text=" ".join(lines)
    m=re.search(r"\b(\d{1,4})\s+(\d{1,4})\b",text)
    if m: return f"{m.group(1)} x {m.group(2)}"
    return ""

# ==========================================================
# TAG & PO
# ==========================================================
def extract_tag(raw):
    m=re.search(r"\b\d{6}(?:[-.,]\d+)?\b",raw)
    return m.group(0) if m else ""

def extract_po(raw):
    m=re.findall(r"\b\d{3,6}-\d{3}\b",raw)
    return m[0] if m else ""

# ==========================================================
# EMAIL (BACKGROUND)
# ==========================================================
def send_email(csv_bytes, images):
    msg=EmailMessage()
    msg["From"]=f"{FROM_NAME} <{EMAIL_USER}>"
    msg["To"]=TO_EMAILS
    if CC_EMAILS: msg["Cc"]=CC_EMAILS
    msg["Subject"]=f"Glass Damage Report – {datetime.now():%Y-%m-%d %H:%M}"
    msg.set_content("Batch submitted. Please review attachments.")

    msg.add_attachment(csv_bytes, maintype="text", subtype="csv", filename="glass_report.csv")
    for i,img in enumerate(images):
        msg.add_attachment(img, maintype="image", subtype="jpeg", filename=f"label_{i+1}.jpg")

    with smtplib.SMTP_SSL("smtp.gmail.com",465) as smtp:
        smtp.login(EMAIL_USER,EMAIL_PASS)
        smtp.send_message(msg)

# ==========================================================
# BACKGROUND QUEUE
# ==========================================================
def process_queue():
    if st.session_state.task_queue:
        job=st.session_state.task_queue.pop(0)
        send_email(job["csv_bytes"], job["images"])

# ==========================================================
# UI
# ==========================================================
st.title("KV Glass Damage Reporter 📸") 

mode=st.radio("Mode:",["Take Photo","Upload Photos"])

if mode=="Take Photo":
    cam=st.camera_input("Capture")
    if cam:
        img=cam.getvalue()
        key=hashlib.sha256(img).hexdigest()
        if key not in st.session_state.added_keys:
            st.session_state.batch.append({"img":img,"key":key,"qty":"","reason":""})
            st.session_state.added_keys.add(key)

if mode=="Upload Photos":
    files=st.file_uploader("Upload",type=["jpg","jpeg","png"],accept_multiple_files=True)
    if files:
        for f in files:
            img=f.read()
            key=hashlib.sha256(img).hexdigest()
            if key not in st.session_state.added_keys:
                st.session_state.batch.append({"img":img,"key":key,"qty":"","reason":""})
                st.session_state.added_keys.add(key)

st.subheader("Photos Added:")

if not st.session_state.batch:
    st.info("No photos yet.")
else:
    rm=[]
    for i,e in enumerate(st.session_state.batch):
        st.write(f"### Photo {i+1}")
        st.image(e["img"])
        e["qty"]=st.text_input(f"Qty ({i+1})",e["qty"],key=f"qty{i}")
        e["reason"]=st.radio(f"Reason ({i+1})",
            ["Scratched","Missing","Broken","KV Production Issue"],
            key=f"reason{i}")
        if st.button(f"Remove {i+1}",key=f"rm{i}"): rm.append(i)

    for i in sorted(rm,reverse=True):
        st.session_state.added_keys.remove(st.session_state.batch[i]["key"])
        del st.session_state.batch[i]

if st.session_state.batch:
    if st.button("Send Batch"):
        images=[]; buf=io.StringIO()
        writer=csv.writer(buf)
        writer.writerow(["index","Thickness","Type","Tint","Size","Tag#","PO#","Qty","Reason"])

        for i,e in enumerate(st.session_state.batch,start=1):
            img=e["img"]
            images.append(img)
            raw=ocr_raw(preprocess(img))

            thickness,glass_type,tint=extract_type_components(raw)
            size=extract_size(raw)
            tag=extract_tag(raw)
            po=extract_po(raw)
            qty=e["qty"]
            reason=e["reason"]

            writer.writerow([i,thickness,glass_type,tint,size,tag,po,qty,reason])

        csv_bytes=buf.getvalue().encode()
        st.session_state.task_queue.append({"csv_bytes":csv_bytes,"images":images})

        st.session_state.added_keys=set()
        st.session_state.batch=[]
        st.success("Batch submitted!")

process_queue()
