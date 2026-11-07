"""Step 4: a small web page for searching (Gemini version, with like/dislike feedback).

Run:  streamlit run app.py
Streamlit re-runs this whole file from top to bottom every time the user types or clicks.
st.session_state is a small memory that survives those re-runs.
"""
import streamlit as st

from feedback import (forget_dislike, forget_similar, hidden_for, load_index, load_memory,
                      remember_dislike, save_memory, search_with_feedback)
from gemini_search import MAX_QUERY_CHARS, _load, embed_query

st.set_page_config(page_title="GIF Search", layout="wide")
st.title("GIF Search")
st.caption("Describe the GIF in your own words. 👍 or 👎 a GIF and the results update to match.")


@st.cache_resource  # run this ONCE per app start, not on every click
def get_index():
    """Load every GIF's saved Gemini vector into memory."""
    _, collection = _load()
    return load_index(collection)


# The page's memory. setdefault only fills in a value the very first time.
st.session_state.setdefault("query", "")  # the search these results belong to
st.session_state.setdefault("query_vector", None)  # Gemini's vector for that search
st.session_state.setdefault("liked", [])  # GIF ids the user liked
st.session_state.setdefault("disliked", [])  # GIF ids the user disliked in THIS search


# Button callbacks. Streamlit runs these when a button is clicked, then re-runs the page,
# so the results re-rank straight away.
def toggle(gif_id, choice):
    """choice is "liked" or "disliked". A second click on the same button removes it."""
    other = "disliked" if choice == "liked" else "liked"
    if gif_id in st.session_state[choice]:
        st.session_state[choice].remove(gif_id)  # second click: undo
    else:
        st.session_state[choice].append(gif_id)
        if gif_id in st.session_state[other]:
            st.session_state[other].remove(gif_id)  # can't be liked AND disliked

    # Memory: save a 👎 together with this search, so similar searches later hide that GIF.
    # Undoing the 👎 (or switching it to 👍) removes it from the memory again.
    query = st.session_state["query"]
    memory = forget_dislike(load_memory(), gif_id, query)
    if gif_id in st.session_state["disliked"]:
        memory = remember_dislike(memory, gif_id, query, st.session_state["query_vector"])
    save_memory(memory)


def reset_feedback():
    """Forget this search's likes and dislikes (used when a new search starts)."""
    st.session_state["liked"] = []
    st.session_state["disliked"] = []


def undo_this_search():
    """The Reset button: undo every click in this search, including saved dislikes."""
    memory = load_memory()
    for gif_id in st.session_state["disliked"]:
        memory = forget_dislike(memory, gif_id, st.session_state["query"])
    save_memory(memory)
    reset_feedback()


def unhide_similar():
    """Forget the remembered dislikes that apply to this search. Other searches keep theirs."""
    save_memory(forget_similar(load_memory(), st.session_state["query_vector"]))
    reset_feedback()


# Input widgets. Each returns the current value.
query = st.text_input("Describe a GIF", placeholder="a dog being confused", max_chars=MAX_QUERY_CHARS)
k = st.slider("Results", 3, 20, 9)

if query.strip():
    # Only call Gemini when the search text changes. Clicks and the slider reuse the saved vector.
    if query != st.session_state["query"]:
        try:
            get_index()  # load the saved GIF vectors (only slow the very first time)
            st.session_state["query_vector"] = embed_query(query)
        except RuntimeError as e:  # raised by gemini_search.py when the index has not been built
            st.error(str(e))
            st.stop()
        except Exception as e:  # e.g. missing GEMINI_API_KEY or no internet
            st.error(f"Gemini could not embed the query: {e}")
            st.stop()
        st.session_state["query"] = query
        reset_feedback()  # a new search starts with no likes or dislikes

    # GIFs disliked in EARLIER, SIMILAR searches are hidden. Ones disliked in THIS search stay
    # visible (pushed down by the algorithm), so you can still click 👎 again to undo.
    hidden_from_before = (hidden_for(load_memory(), st.session_state["query_vector"])
                          - set(st.session_state["disliked"]))

    results = search_with_feedback(
        st.session_state["query_vector"], get_index(), k,
        st.session_state["liked"], st.session_state["disliked"], hidden_from_before,
    )

    # Feedback summary + buttons.
    info, reset, unhide = st.columns([3, 1, 1])
    info.write(f"👍 {len(st.session_state['liked'])} liked · 👎 {len(st.session_state['disliked'])} disliked"
               f" · 🙈 {len(hidden_from_before)} hidden from similar searches")
    reset.button("Reset feedback", on_click=undo_this_search)
    unhide.button("Show hidden GIFs again", on_click=unhide_similar, disabled=not hidden_from_before)

    # Show the GIFs in a grid of 3 columns.
    columns = st.columns(3)
    for i, r in enumerate(results):
        with columns[i % 3]:  # i % 3 cycles through columns 0, 1, 2, 0, 1, 2 ...
            st.image(r["url"], width="stretch")
            st.caption(f"{r['description']} ({r['score']:.2f})")

            # A selected button is shown filled in (type="primary").
            is_liked = r["id"] in st.session_state["liked"]
            is_disliked = r["id"] in st.session_state["disliked"]
            up, down = st.columns(2)
            up.button("👍", key=f"up_{r['id']}", on_click=toggle, args=(r["id"], "liked"),
                      type="primary" if is_liked else "secondary")
            down.button("👎", key=f"down_{r['id']}", on_click=toggle, args=(r["id"], "disliked"),
                        type="primary" if is_disliked else "secondary")
