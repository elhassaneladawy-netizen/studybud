"""StudBud: upload a book PDF, get explanations, quizzes and answers."""

import io
import json
import os
import sys

import streamlit as st
from google import genai
from google.genai import errors, types
from pypdf import PdfReader, PdfWriter


def _load_key():
    key = os.getenv("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        return str(st.secrets["GEMINI_API_KEY"])
    except Exception:
        return ""


API_KEY = _load_key()
MODEL = "gemini-3.5-flash-lite"  # locked: not configurable
MAX_PAGES_PER_REQUEST = 15

GRADES = [
    "Grade 1-3", "Grade 4-6", "Grade 7-9", "Grade 10-12",
    "University / College", "Adult learner",
]
LANGUAGES = [
    "English", "Arabic", "Spanish", "French", "German", "Portuguese",
    "Turkish", "Hindi", "Urdu", "Indonesian", "Russian",
    "Chinese (Simplified)", "Japanese", "Korean", "Italian",
]
RTL_LANGUAGES = {"Arabic", "Urdu"}


def slice_pdf(file_bytes, start, end):
    reader = PdfReader(io.BytesIO(file_bytes))
    writer = PdfWriter()
    for i in range(start - 1, end):
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_system_prompt(grade, language, country, subject):
    parts = [
        "You are StudBud, a patient, friendly AI study tutor.",
        "If asked who or what you are, say you are StudBud, an AI tutor. Never claim to be a human.",
        "If asked which company, model or technology is behind you, say you are StudBud "
        "and that you can't share details about your technology.",
        f"The student's level is: {grade}.",
        f"Write ALL of your output in {language}, even if the book is in another language.",
        "Base everything strictly on the pages provided. If something is not in the pages, say so.",
        "Write math with plain readable notation. Describe diagrams and charts in words when they matter.",
    ]
    if country.strip():
        parts.append(
            f"The student studies in {country.strip()}. Use familiar examples and units for that place."
        )
    if subject.strip():
        parts.append(f"The subject is: {subject.strip()}.")
    else:
        parts.append("Work out the subject from the pages and teach in a way that suits it.")
    return " ".join(parts)


def ask_ai(client, system, pdf_bytes, history, json_mode=False):
    """history: list of {"role": "user"|"assistant", "content": str}.
    The PDF is attached to the first user message."""
    contents = []
    for i, turn in enumerate(history):
        role = "user" if turn["role"] == "user" else "model"
        parts = []
        if i == 0:
            parts.append(types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"))
        parts.append(types.Part.from_text(text=turn["content"]))
        contents.append(types.Content(role=role, parts=parts))

    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=8192,
        response_mime_type="application/json" if json_mode else None,
    )
    response = client.models.generate_content(model=MODEL, contents=contents, config=config)
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("empty answer")
    return text


def show_error(e):
    """Show a friendly message to students. Technical details go to the terminal only."""
    print(f"[StudBud error] {type(e).__name__}: {e}", file=sys.stderr)
    code = getattr(e, "code", None) if isinstance(e, errors.APIError) else None
    if code == 429:
        st.error("StudBud is busy right now. Please wait a minute and try again, or choose fewer pages.")
    else:
        st.error("Something went wrong. Please try again in a moment.")


def parse_quiz(raw):
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON list found in the reply.")
    data = json.loads(raw[start:end + 1])
    quiz = []
    for q in data:
        options = q["options"]
        idx = int(q["answer_index"])
        if not isinstance(options, list) or len(options) < 2 or not 0 <= idx < len(options):
            raise ValueError("A question had invalid options or answer.")
        quiz.append({
            "question": str(q["question"]),
            "options": [str(o) for o in options],
            "answer_index": idx,
            "explanation": str(q.get("explanation", "")),
        })
    if not quiz:
        raise ValueError("The quiz was empty.")
    return quiz


def rtl_wrap(text, language):
    if language in RTL_LANGUAGES:
        return f'<div dir="rtl" style="text-align:right">\n\n{text}\n\n</div>'
    return text


st.set_page_config(page_title="StudBud", page_icon="🎓", layout="wide")
st.markdown(
    """
    <style>
/* studbud-theme */
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;600;700&display=swap');
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {display: none;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stHeader"] {background: transparent;}
.stApp {background: radial-gradient(ellipse 1000px 520px at 50% -140px, rgba(217,70,239,0.38), rgba(124,58,237,0.22) 45%, rgba(6,5,11,0) 72%), #06050b;}
.stApp, .stApp p, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp input, .stApp textarea, .stApp button {font-family: 'Sora', sans-serif;}
.block-container {padding-top: 2.2rem; max-width: 1100px;}
h1, h2, h3 {font-weight: 400; letter-spacing: -0.02em;}
.sb-logo {display: flex; align-items: center; gap: 12px; font-size: 30px; font-weight: 700; margin-bottom: 4px;}
.sb-mark {position: relative; width: 30px; height: 28px; display: inline-block;}
.sb-mark i {position: absolute; width: 17px; height: 11px; border-radius: 4px;}
.sb-mark i:first-child {left: 0; bottom: 0; background: #8b5cf6;}
.sb-mark i:last-child {left: 11px; top: 0; background: #60a5fa;}
[data-testid="stSidebar"] {background: rgba(11,9,20,0.92); border-right: 1px solid rgba(255,255,255,0.08);}
[data-testid="stSidebar"] label p {color: #a59fc2; font-size: 13px;}
[data-baseweb="select"] > div, [data-baseweb="input"] > div {background-color: rgba(255,255,255,0.07); border-radius: 10px; border-color: rgba(255,255,255,0.08);}
.stTabs [data-baseweb="tab-list"] {gap: 8px; border-bottom: none;}
.stTabs [data-baseweb="tab"] {height: auto; padding: 7px 18px; border-radius: 99px; background: rgba(255,255,255,0.07); color: #a59fc2;}
.stTabs [aria-selected="true"] {background: #a78bfa; color: #ffffff;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {display: none;}
.stButton button, .stFormSubmitButton button {border-radius: 12px; padding: 0.55rem 1.4rem; font-weight: 600; background: transparent; border: 1px solid rgba(255,255,255,0.25); color: #ffffff;}
.stButton button:hover, .stFormSubmitButton button:hover {border-color: #a78bfa; color: #ffffff;}
.stButton button[kind="primary"], .stButton button[data-testid="stBaseButton-primary"] {background: #ffffff; border-color: #ffffff; color: #0b0914;}
.stButton button[kind="primary"] p, .stButton button[data-testid="stBaseButton-primary"] p {color: #0b0914;}
.stButton button[kind="primary"]:hover, .stButton button[data-testid="stBaseButton-primary"]:hover {background: #ede9fe; border-color: #ede9fe;}
[data-testid="stFileUploaderDropzone"] {background: rgba(255,255,255,0.04); border: 1px dashed rgba(167,139,250,0.55); border-radius: 16px;}
[data-testid="stForm"] {border: 1px solid rgba(255,255,255,0.10); border-radius: 18px; background: rgba(255,255,255,0.03);}
[data-testid="stAlert"] {border-radius: 14px;}
[data-testid="stChatMessage"] {background: rgba(255,255,255,0.04); border-radius: 16px;}
[data-testid="stChatInput"] {border-radius: 16px;}
.block-container {padding-bottom: 3rem;}
.block-container::after {content: 'Made by Alhassan Eladawy'; display: block; text-align: center; margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid rgba(255,255,255,0.08); color: #8f89ad; font-size: 13px;}
</style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="sb-logo"><span class="sb-mark"><i></i><i></i></span><span>StudBud</span></div>', unsafe_allow_html=True)
st.caption("Upload a few pages of your textbook. Get explanations, quizzes and answers.")

if not API_KEY:
    print("[StudBud error] GEMINI_API_KEY is not set.", file=sys.stderr)
    st.error("StudBud is not available right now. Please try again later.")
    st.stop()

with st.sidebar:
    st.header("About you")
    grade = st.selectbox("Your level", GRADES, index=2)
    language = st.selectbox("Explain in", LANGUAGES)
    country = st.text_input("Country / curriculum (optional)", placeholder="e.g. Egypt, Mexico, UK GCSE")
    subject = st.text_input("Subject (optional)", placeholder="Leave empty to auto-detect")
    st.caption("The pages you upload are processed to create your explanations and quizzes.")

uploaded = st.file_uploader("Upload your book (PDF)", type=["pdf"])

if not uploaded:
    st.info("Upload a PDF to begin.")
    st.stop()

file_bytes = uploaded.getvalue()
try:
    total_pages = len(PdfReader(io.BytesIO(file_bytes)).pages)
except Exception:
    st.error("Could not read this PDF. It may be corrupted or password-protected.")
    st.stop()

st.write(f"**{uploaded.name}**: {total_pages} pages")

col1, col2 = st.columns(2)
start_page = col1.number_input("From page", min_value=1, max_value=total_pages, value=1, step=1)
end_default = min(total_pages, int(start_page) + 4)
end_page = col2.number_input(
    "To page", min_value=1, max_value=total_pages, value=end_default, step=1
)

start_page, end_page = int(start_page), int(end_page)
if end_page < start_page:
    st.error("'To page' must be the same as or after 'From page'.")
    st.stop()
if end_page - start_page + 1 > MAX_PAGES_PER_REQUEST:
    st.error(f"Please choose at most {MAX_PAGES_PER_REQUEST} pages at a time.")
    st.stop()

client = genai.Client(api_key=API_KEY)
system_prompt = build_system_prompt(grade, language, country, subject)

selection_key = (uploaded.name, len(file_bytes), start_page, end_page)
if st.session_state.get("selection_key") != selection_key:
    st.session_state.selection_key = selection_key
    st.session_state.explanation = None
    st.session_state.quiz = None
    st.session_state.quiz_result = None
    st.session_state.chat = []

try:
    pdf_slice = slice_pdf(file_bytes, start_page, end_page)
except Exception as e:
    print(f"[StudBud error] slice failed: {e}", file=sys.stderr)
    st.error("Could not read those pages. Please try a different range.")
    st.stop()

tab_explain, tab_quiz, tab_chat = st.tabs(["Explain", "Quiz", "Ask a question"])

with tab_explain:
    if st.button("Explain these pages", type="primary"):
        with st.spinner("Reading and explaining..."):
            try:
                st.session_state.explanation = ask_ai(
                    client, system_prompt, pdf_slice,
                    [{"role": "user", "content": (
                        "Explain these pages to me step by step. Start with a short summary, "
                        "then explain the key ideas simply with examples, and end with "
                        "a list of the most important terms."
                    )}],
                )
            except (errors.APIError, RuntimeError) as e:
                show_error(e)
    if st.session_state.explanation:
        st.markdown(rtl_wrap(st.session_state.explanation, language), unsafe_allow_html=True)

with tab_quiz:
    num_q = st.slider("Number of questions", 3, 10, 5)
    if st.button("Make a quiz"):
        with st.spinner("Writing questions..."):
            try:
                raw = ask_ai(
                    client, system_prompt, pdf_slice,
                    [{"role": "user", "content": (
                        f"Write {num_q} multiple-choice questions testing understanding of these pages. "
                        "Reply with ONLY a JSON list, no other text. "
                        'Each item must look like: {"question": "...", "options": ["...", "...", "...", "..."], '
                        '"answer_index": 0, "explanation": "..."}. '
                        "answer_index is the zero-based position of the correct option. "
                        "Use exactly 4 options and vary which position is correct."
                    )}],
                    json_mode=True,
                )
                st.session_state.quiz = parse_quiz(raw)
                st.session_state.quiz_result = None
            except (errors.APIError, RuntimeError) as e:
                show_error(e)
            except (ValueError, KeyError, TypeError):
                st.session_state.quiz = None
                st.error("The quiz didn't come out right. Please click 'Make a quiz' again.")

    quiz = st.session_state.quiz
    if quiz:
        with st.form("quiz_form"):
            picks = []
            for i, q in enumerate(quiz):
                picks.append(st.radio(
                    f"{i + 1}. {q['question']}",
                    options=list(range(len(q["options"]))),
                    format_func=lambda k, opts=q["options"]: opts[k],
                    index=None,
                    key=f"q_{i}",
                ))
            submitted = st.form_submit_button("Check answers")
        if submitted:
            st.session_state.quiz_result = picks

        result = st.session_state.quiz_result
        if result is not None:
            score = sum(1 for p, q in zip(result, quiz) if p == q["answer_index"])
            st.subheader(f"Score: {score} / {len(quiz)}")
            for i, (p, q) in enumerate(zip(result, quiz)):
                correct_text = q["options"][q["answer_index"]]
                if p == q["answer_index"]:
                    st.success(f"{i + 1}. Correct. {q['explanation']}")
                elif p is None:
                    st.warning(f"{i + 1}. Not answered. Correct answer: {correct_text}. {q['explanation']}")
                else:
                    st.error(f"{i + 1}. Wrong. Correct answer: {correct_text}. {q['explanation']}")

with tab_chat:
    for turn in st.session_state.chat:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    question = st.chat_input("Ask anything about these pages")
    if question:
        st.session_state.chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    answer = ask_ai(client, system_prompt, pdf_slice, st.session_state.chat)
                    st.markdown(answer)
                    st.session_state.chat.append({"role": "assistant", "content": answer})
                except (errors.APIError, RuntimeError) as e:
                    show_error(e)
                    st.session_state.chat.pop()
