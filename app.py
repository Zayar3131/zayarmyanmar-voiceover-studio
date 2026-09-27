import streamlit as st
import edge_tts
import asyncio
import io
import re
import time
from mutagen.mp3 import MP3
from pydub import AudioSegment

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


def transcribe_via_hf(audio_bytes, hf_token, content_type="audio/mpeg"):
    import requests
    import base64
    api_url = "https://router.huggingface.co/hf-inference/models/openai/whisper-large-v3"
    headers = {
        "Authorization": f"Bearer {hf_token}",
        "Content-Type": "application/json",
        "X-Wait-For-Model": "true",
    }
    payload = {
        "inputs": base64.b64encode(audio_bytes).decode("utf-8"),
        "parameters": {
            "return_timestamps": "word",
            "chunk_length_s": 30,
            "stride_length_s": 5,
            "generate_kwargs": {"language": "my", "task": "transcribe"},
        },
    }
    resp = requests.post(api_url, headers=headers, json=payload, timeout=600)
    if resp.status_code != 200:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:300]}")
    result = resp.json()
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(result["error"])
    words = []
    for chunk in result.get("chunks", []):
        ts = chunk.get("timestamp", [None, None])
        if ts[0] is None or ts[1] is None:
            continue
        words.append({"start": ts[0], "end": ts[1], "text": chunk["text"]})
    return words, result.get("text", "")


def split_audio_chunks(audio_bytes, chunk_seconds=60):
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
    chunk_ms = chunk_seconds * 1000
    chunks = []
    for start_ms in range(0, len(audio), chunk_ms):
        piece = audio[start_ms:start_ms + chunk_ms]
        buf = io.BytesIO()
        piece.export(buf, format="mp3")
        chunks.append((start_ms / 1000.0, buf.getvalue()))
    return chunks


def transcribe_long_audio_via_hf(audio_bytes, hf_token, progress_cb=None, chunk_seconds=60):
    chunks = split_audio_chunks(audio_bytes, chunk_seconds)
    all_words = []
    for i, (offset_sec, chunk_bytes) in enumerate(chunks):
        if progress_cb:
            progress_cb(i, len(chunks))
        words, _ = transcribe_via_hf(chunk_bytes, hf_token, "audio/mpeg")
        for w in words:
            all_words.append({
                "start": w["start"] + offset_sec,
                "end": w["end"] + offset_sec,
                "text": w["text"],
            })
        time.sleep(1)
    if progress_cb:
        progress_cb(len(chunks), len(chunks))
    return all_words


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
    st.caption("မိမိသီချင်းသွင်းထားသော (voice clone) အသံဖိုင်ကို တင်ပြီး တိကျသော SRT ဖိုင် ထုတ်ယူနိုင်ပါသည် (Whisper large-v3, Hugging Face free API)")
    hf_token = st.text_input("🔑 Hugging Face Access Token", type="password",
                              help="huggingface.co → Settings → Access Tokens မှာ အခမဲ့ ယူနိုင်ပါသည်")
    audio_file = st.file_uploader("🎧 အသံဖိုင် တင်ပါ", type=["mp3", "wav", "m4a", "ogg"])
    words_per_cue2 = st.slider("📝 တစ်ကြောင်းလျှင် စာလုံးအရေအတွက်", 3, 15, 8, key="wpc2")

    if "stt_srt" not in st.session_state:
        st.session_state.stt_srt = None

    if st.button("📝 SRT ထုတ်မည်", type="primary", use_container_width=True,
                 disabled=(audio_file is None or not hf_token)):
        try:
            audio_file.seek(0)
            audio_bytes = audio_file.read()
            progress_bar = st.progress(0, text="အသံဖိုင် ဖြတ်နေသည်...")

            def update_progress(done, total):
                pct = int((done / total) * 100) if total else 0
                progress_bar.progress(pct, text=f"အပိုင်း {done}/{total} — transcribe လုပ်နေသည်...")

            words = transcribe_long_audio_via_hf(audio_bytes, hf_token, update_progress, chunk_seconds=60)
            progress_bar.empty()
            if words:
                st.session_state.stt_srt = build_srt_from_whisper_words(words, words_per_cue2)
                st.success("ပြီးပါပြီ ✅")
            else:
                st.warning("Word-timing data မတွေ့ပါ")
        except Exception as e:
            st.error(f"မအောင်မြင်ပါ — {e}")
            st.caption("Token မှားနေခြင်း၊ model cold-start ဖြစ်နေခြင်း (ခဏနေမှ ပြန်စမ်းပါ)၊ သို့မဟုတ် ffmpeg ပြသနာ ဖြစ်နိုင်ပါသည်")

    if st.session_state.stt_srt:
        st.download_button("📝 SRT ဖိုင် ဒေါင်းလုတ်", st.session_state.stt_srt, file_name="transcript.srt",
                            mime="text/plain", use_container_width=True, key="dl_stt_srt")
        with st.expander("SRT preview"):
            st.text(st.session_state.stt_srt[:2000])

st.caption("Myanmar Voiceover Studio · Edge TTS + Whisper STT · Unlimited Words")
