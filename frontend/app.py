"""Streamlit test UI for the GenAI Data Assistant API."""
import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
ICON = {"document": "📄 documents", "database": "🗄️ database", "both": "📄+🗄️ documents + database"}

st.set_page_config(page_title="GenAI Data Assistant", page_icon="🤖", layout="wide")


def call(method, path, **kw):
    try:
        r = requests.request(method, f"{API}{path}", timeout=kw.pop("timeout", 30), **kw)
        return r
    except requests.RequestException as e:
        st.error(f"API unreachable: {e}")
        return None


def detail(r):
    try:
        return r.json().get("detail", r.text)
    except ValueError:
        return r.text


def render_extras(m):
    st.caption(f"Route: {ICON.get(m['route'], m['route'])}")
    if m.get("standalone") and m["standalone"] != m.get("question"):
        st.caption(f"Interpreted as: _{m['standalone']}_")
    if m.get("sources"):
        with st.expander(f"Sources ({len(m['sources'])})"):
            for s in m["sources"]:
                page = f", page {s['page']}" if s.get("page") else ""
                st.markdown(f"**{s['filename']}**{page}")
                if s.get("section"):
                    heading = f" › {s['heading']}" if s.get("heading") and s["heading"] != s["section"] else ""
                    st.markdown(f"Section: {s['section']}{heading}")
                st.markdown(f"Score: {s['score']}")
                if s.get("debug"):  # only present when the API runs with RAG_DEBUG=true
                    d = s["debug"]
                    st.caption(f"debug · semantic {d['semantic']} · keyword {d['keyword']} · final {d['final']} · "
                               f"matched: {', '.join(d['matched']) or '-'} · chunk {d['chunk_index']}")
                st.caption(s["snippet"])
    sql = m.get("sql")
    if sql:
        with st.expander("SQL"):
            st.code(sql["query"] or "(no query produced)", language="sql")
            if sql.get("error"):
                st.warning(sql["error"])
            elif sql["rows"]:
                st.dataframe(pd.DataFrame(sql["rows"], columns=sql["columns"]), use_container_width=True)
            else:
                st.caption("0 rows")


# ---- sidebar ---------------------------------------------------------------
with st.sidebar:
    st.header("Status")
    h = call("GET", "/health", timeout=10)
    if h is not None:
        for name, state in h.json().get("services", {}).items():
            st.write(("🟢" if state == "ok" else "🔴") + f" {name}")

    st.header("Documents")
    r = call("GET", "/documents")
    if r is not None and r.ok:
        for d in r.json():
            c1, c2 = st.columns([4, 1])
            c1.caption(f"{d['filename']} ({d['chunks']} chunks)")
            if c2.button("🗑", key=d["id"], help="Delete"):
                call("DELETE", f"/documents/{d['id']}")
                st.rerun()
    up = st.file_uploader("Upload & index", type=["pdf", "docx", "txt", "md"])
    if up and st.button("Ingest file"):
        with st.spinner("Embedding…"):
            r = call("POST", "/documents/ingest", files={"file": (up.name, up.getvalue())}, timeout=300)
        if r is not None:
            st.success("Indexed") if r.ok else st.error(detail(r))
            st.rerun() if r.ok else None
    if st.button("Re-index data/documents folder"):
        with st.spinner("Indexing…"):
            call("POST", "/documents/ingest", timeout=600)
        st.rerun()

    st.header("Try")
    examples = [
        "What is the company's leave policy?",
        "Which are the top 5 customers by revenue?",
        "What is the refund policy and how much was refunded last month?",
        "Show monthly revenue for the last 3 months",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state["pending"] = ex
    if st.button("🧹 New conversation", use_container_width=True):
        st.session_state.pop("messages", None)
        st.session_state.pop("session_id", None)
        st.rerun()

# ---- chat ------------------------------------------------------------------
st.title("🤖 Local GenAI Data Assistant")
st.caption("Ask about documents, the sales database, or both. Everything runs locally.")
messages = st.session_state.setdefault("messages", [])

for m in messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_extras(m)

prompt = st.chat_input("Ask a question…") or st.session_state.pop("pending", None)
if prompt:
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking… (local model, can take up to a minute)"):
            body = {"message": prompt}
            if "session_id" in st.session_state:
                body["session_id"] = st.session_state["session_id"]
            r = call("POST", "/chat", json=body, timeout=600)
        if r is None:
            pass
        elif not r.ok:
            st.error(f"{r.status_code}: {detail(r)}")
        else:
            data = r.json()
            st.session_state["session_id"] = data["session_id"]
            m = {"role": "assistant", "content": data["answer"], "route": data["route"],
                 "standalone": data["standalone_question"], "question": prompt,
                 "sources": data["sources"], "sql": data["sql"]}
            messages.append(m)
            st.markdown(m["content"])
            render_extras(m)
