import streamlit as st
import edge_tts
import asyncio

st.set_page_config(page_title="မြန်မာ Voiceover Studio", page_icon="🎙️", layout="centered")

VOICES = {
    "Thiha (သီဟ) — ကျား": "my-MM-ThihaNeural",
    "Nilar (နီလာ) — မ": "my-MM-NilarNeural",
}

st.title("🎙️ မြန်မာ Voiceover Studio")
st.caption("Microsoft Edge TTS · အသံ + SRT ဖိုင် တစ်ခါတည်း ထုတ်ပေးသည်")

text = st.text_area("📝 SCRIPT / စာသား", height=220, placeholder="သင့်စာသားကို ဒီနေရာမှာ ရိုက်ထည့်ပါ...")

voice_label = st.radio("🎭 VOICE ရွေးချယ်ရန်", list(VOICES.keys()))
voice = VOICES[voice_label]

col1, col2 = st.columns(2)
with col1:
    rate = st.slider("⚡ အမြန်နှုန်း (RATE)", -50, 50, 0)
with col2:
    pitch = st.slider("🎵 အသံမြင့်နိမ့် (PITCH)", -50, 50, 0)

words_per_cue = st.slider("📝 တစ်ကြောင်းလျှင် စာလုံးအရေအတွက် (SRT)", 3, 15, 8)


def fmt(v, unit):
    return f"{'+' if v >= 0 else ''}{v}{unit}"


def ms_to_srt_time(ms):
    h = int(ms // 3600000)
    m = int((ms % 3600000) // 60000)
    s = int((ms % 60000) // 1000)
    msec = int(ms % 1000)
    return f"{h:02}:{m:02}:{s:02},{msec:03}"


def build_srt(words, per_cue):
    lines = []
    idx = 1
    i = 0
    enders = ("။", "၊", ".", "!", "?")
    while i < len(words):
        group = [words[i]]
        i += 1
        while i < len(words) and len(group) < per_cue and not group[-1]["text"].strip().endswith(enders):
            group.append(words[i])
            i += 1
        start = group[0]["offset"] / 10000
        end = (group[-1]["offset"] + group[-1]["duration"]) / 10000
        text_line = "".join(w["text"] for w in group).strip()
        lines.append(f"{idx}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{text_line}\n")
        idx += 1
    return "\n".join(lines)


async def synthesize(text, voice, rate_str, pitch_str):
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    audio_bytes = b""
    words = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            words.append(chunk)
    return audio_bytes, words


if st.button("🎙️ Generate Voiceover", type="primary", use_container_width=True):
    if not text.strip():
        st.warning("စာသား ထည့်ပါ")
    else:
        with st.spinner("အသံ ထုတ်နေသည်..."):
            audio_bytes, words = asyncio.run(
                synthesize(text, voice, fmt(rate, "%"), fmt(pitch, "Hz"))
            )
            srt_text = build_srt(words, words_per_cue)
        st.success("ပြီးပါပြီ ✅")
        st.audio(audio_bytes, format="audio/mp3")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("🔊 Audio (MP3)", audio_bytes, file_name="voiceover.mp3",
                                mime="audio/mpeg", use_container_width=True)
        with c2:
            st.download_button("📝 SRT ဖိုင်", srt_text, file_name="voiceover.srt",
                                mime="text/plain", use_container_width=True)
        with st.expander("SRT preview"):
            st.text(srt_text[:2000])

st.caption("Myanmar Voiceover Studio · Edge TTS · my-MM-ThihaNeural / my-MM-NilarNeural · Unlimited Words")
