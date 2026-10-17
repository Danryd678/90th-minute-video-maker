"""The 90th Minutes Tv — Video Maker. Paste a script, get a finished Short."""
import os
import shutil
import tempfile
import time

import streamlit as st

from videomaker import VOICES, build_video

st.set_page_config(page_title="90th Minutes Tv — Video Maker", page_icon="⚽")

st.title("⚽ The 90th Minutes Tv — Video Maker")
st.caption("Paste a script → get a finished vertical video. Free, no editing needed.")

DEFAULT_SCRIPT = """He scored Nigeria's first ever World Cup goal. 1994. USA. He grabbed the net with both hands and screamed. Pure joy. But behind that smile was pain. Yekini gave everything to football — and when the cheers faded, the world forgot him. He died in 2012, alone and struggling. Nigeria's greatest striker deserved a better ending. What's your favourite Yekini memory? Follow The 90th Minutes Tv for more."""

script = st.text_area("Your script (one video = one script)", value=DEFAULT_SCRIPT,
                      height=220, max_chars=1200)
voice_label = st.selectbox("Voiceover voice", list(VOICES.keys()), index=0)

if st.button("🎬 Make my video", type="primary"):
    script = script.strip()
    if not script:
        st.error("Paste a script first.")
        st.stop()
    workdir = tempfile.mkdtemp(prefix="ninetieth_")
    status = st.empty()
    bar = st.progress(0)

    def progress(msg, frac=None):
        status.info(msg)

    try:
        progress("🎙️ Making the voiceover...")
        bar.progress(10)
        # Streamlit Cloud has normal internet; pass proxy only if set (local dev)
        proxy = os.environ.get("TTS_PROXY")
        final = build_video(script, VOICES[voice_label], workdir,
                            progress=progress, proxy=proxy)
        bar.progress(100)
        status.success("Done! Your video is ready. 🎉")
        st.video(final)
        with open(final, "rb") as f:
            st.download_button("⬇️ Download video", f, file_name="90th-minute-video.mp4",
                               mime="video/mp4")
        st.caption("Upload it to YouTube — vertical + under 3 minutes posts as a Short automatically.")
    except Exception as e:
        status.error(f"Something went wrong: {e}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
