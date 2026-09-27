import streamlit as st
import edge_tts
import asyncio
import io
import re
from mutagen.mp3 import MP3

st.set_page_config(page_title="မြန်မာ Voiceover Studio", page_icon="🎙️", layout="centered")

VOICES = {
    "Thiha (သီဟ) — ကျား": "my-MM-ThihaNeural",
    "Nilar (နီလာ) — မ": "my-MM-NilarNeural",
}


def fmt(v, unit):
    return f"{'+' if v >= 0 else ''}{v}{unit}"


def ms_to_srt_time(ms):
    ms = max(0, ms)
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


def build_srt_fallback(text, total_ms, per_cue):
    words = re.findall(r"\S+", text)
    if not words or total_ms <= 0:
        return ""
    groups = [words[i:i + per_cue] for i in range(0, len(words), per_cue)]
    ms_per_word = total_ms / len(words)
    lines = []
    cursor = 0.0
    for idx, g in enumerate(groups, start=1):
        start = cursor
        end = cursor + ms_per_word * len(g)
        lines.append(f"{idx}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{' '.join(g)}\n")
        cursor = end
    return "\n".join(lines)


def build_srt_from_whisper_words(words, per_cue):
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
        start = group[0]["start"] * 1000
        end = group[-1]["end"] * 1000
        text_line = " ".join(w["text"].strip() for w in group).strip()
        lines.append(f"{idx}\n{ms_to_srt_time(start)} --> {ms_to_srt_time(end)}\n{text_line}\n")
        idx += 1
    return "\n".join(lines)


async def synthesize(text, voice, rate_str, pitch_str, volume_str):
    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str, volume=volume_str)
    audio_bytes = b""
    words = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            words.append(chunk)
    return audio_bytes, words


@st.cache_resource(show_spinner=False)
def load_whisper_model(model_size):
    from faster_whisper import WhisperModel
    return WhisperModel(model_size, device="cpu", compute_type="int8")


st.title("🎙️ မြန်မာ Voiceover Studio")
st.caption("Microsoft Edge TTS · အသံ + SRT ဖိုင် တစ်ခါတည်း ထုတ်ပေးသည်")

tab1, tab2 = st.tabs(["🗣️ Text → Voice + SRT", "🎧 Audio Upload → SRT"])

with tab1:
    text = st.text_area("📝 SCRIPT / စာသား", height=220, placeholder="သင့်စာသားကို ဒီနေရာမှာ ရိုက်ထည့်ပါ...", key="tts_text")

    voice_label = st.radio("🎭 VOICE ရွေးချယ်ရန်", list(VOICES.keys()))
    voice = VOICES[voice_label]

    c1, c2, c3 = st.columns(3)
    with c1:
        rate = st.slider("⚡ RATE", -50, 50, 0)
    with c2:
        pitch = st.slider("🎵 PITCH", -50, 50, 0)
    with c3:
        volume = st.slider("🔊 VOLUME", -80, 0, -20)

    words_per_cue = st.slider("📝 တစ်ကြောင်းလျှင် စာလုံးအရေအတွက် (SRT)", 3, 15, 8)

    if "audio_bytes" not in st.session_state:
        st.session_state.audio_bytes = None
        st.session_state.srt_text = None

    if st.button("🎙️ Generate Voiceover", type="primary", use_container_width=True):
        if not text.strip():
            st.warning("စာသား ထည့်ပါ")
        else:
            with st.spinner("အသံ ထုတ်နေသည်..."):
                audio_bytes, words = asyncio.run(
                    synthesize(text, voice, fmt(rate, "%"), fmt(pitch, "Hz"), fmt(volume, "%"))
                )
                if words:
                    srt_text = build_srt(words, words_per_cue)
                else:
                    try:
                        total_ms = MP3(io.BytesIO(audio_bytes)).info.length * 1000
                    except Exception:
                        word_count = max(1, len(text.split()))
                        speed_factor = 1 + (rate / 100)
                        total_ms = (word_count / (1.8 * speed_factor)) * 1000
                    srt_text = build_srt_fallback(text, total_ms, words_per_cue)
                    st.info("Word-timing data မရလို့ SRT ကို အသံဖိုင်ရဲ့ တကယ့်ကြာချိန်ဖြင့် ဖန်တီးထားပါသည်")
            st.session_state.audio_bytes = audio_bytes
            st.session_state.srt_text = srt_text

    if st.session_state.audio_bytes:
        st.success("ပြီးပါပြီ ✅")
        st.audio(st.session_state.audio_bytes, format="audio/mp3")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button("🔊 Audio (MP3)", st.session_state.audio_bytes, file_name="voiceover.mp3",
                                mime="audio/mpeg", use_container_width=True, key="dl_audio")
        with d2:
            st.download_button("📝 SRT ဖိုင်", st.session_state.srt_text, file_name="voiceover.srt",
                                mime="text/plain", use_container_width=True, key="dl_srt")
        with st.expander("SRT preview"):
            st.text(st.session_state.srt_text[:2000])

with tab2:
    st.caption("မိမိသီချင်းသွင်းထားသော (voice clone) အသံဖိုင်ကို တင်ပြီး တိကျသော SRT ဖိုင် ထုတ်ယူနိုင်ပါသည်")
    st.warning("⚠️ Free hosting ဖြစ်၍ model load ချိန်တွင် ပထမဆုံးအကြိမ် နှေးနိုင်ပါသည် (~1-2 မိနစ်)။ Model အကြီးရွေးလျှင် app crash ဖြစ်နိုင်ခြေ ပိုများပါသည်")

    model_size = st.selectbox("🧠 Model အရွယ်အစား", ["tiny", "base", "small"], index=1,
                               help="tiny=အမြန်ဆုံး/တိကျမှုနည်း, small=တိကျမှုပိုများ/နှေးပြီး crash ဖြစ်နိုင်ခြေများ")
    audio_file = st.file_uploader("🎧 အသံဖိုင် တင်ပါ", type=["mp3", "wav", "m4a", "ogg"])
    words_per_cue2 = st.slider("📝 တစ်ကြောင်းလျှင် စာလုံးအရေအတွက်", 3, 15, 8, key="wpc2")

    if "stt_srt" not in st.session_state:
        st.session_state.stt_srt = None

    if st.button("📝 SRT ထုတ်မည်", type="primary", use_container_width=True, disabled=(audio_file is None)):
        with st.spinner("Model ဖွင့်နေသည် (ပထမအကြိမ်ဆို နှေးနိုင်ပါသည်)..."):
            try:
                model = load_whisper_model(model_size)
            except Exception as e:
                st.error(f"Model load မအောင်မြင်ပါ: {e}")
                model = None
        if model:
            with st.spinner("အသံ နားထောင်ပြီး စာသား ထုတ်နေသည်..."):
                try:
                    audio_file.seek(0)
                    segments, info = model.transcribe(audio_file, language="my", word_timestamps=True)
                    words = []
                    for seg in segments:
                        for w in seg.words:
                            words.append({"start": w.start, "end": w.end, "text": w.word})
                    if words:
                        st.session_state.stt_srt = build_srt_from_whisper_words(words, words_per_cue2)
                        st.success(f"ပြီးပါပြီ ✅ (detected language: {info.language})")
                    else:
                        st.warning("စကားသံ မတွေ့ပါ")
                except Exception as e:
                    st.error(f"Transcribe မအောင်မြင်ပါ: {e}")

    if st.session_state.stt_srt:
        st.download_button("📝 SRT ဖိုင် ဒေါင်းလုတ်", st.session_state.stt_srt, file_name="transcript.srt",
                            mime="text/plain", use_container_width=True, key="dl_stt_srt")
        with st.expander("SRT preview"):
            st.text(st.session_state.stt_srt[:2000])

st.caption("Myanmar Voiceover Studio · Edge TTS + Whisper STT · Unlimited Words")
