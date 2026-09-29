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
    ms = max(0, int(round(ms)))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    msec = ms % 1000
    return f"{h:02}:{m:02}:{s:02},{msec:03}"


def prepare_text(t, flat_newlines=True, soft_commas=False):
    if flat_newlines:
        t = re.sub(r"\s*\n\s*", " ", t)
    if soft_commas:
        t = t.replace("၊", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def split_sentences(text, max_len=250):
    raw = re.split(r"(?<=[။!?])\s*", text)
    sents = []
    for s in raw:
        s = s.strip()
        if not s or not re.search(r"\w", s):
            continue
        if len(s) <= max_len:
            sents.append(s)
            continue
        buf = ""
        for p in re.split(r"(?<=၊)\s*", s):
            if buf and len(buf) + len(p) > max_len:
                sents.append(buf.strip())
                buf = p
            else:
                buf = (buf + " " + p).strip() if buf else p
        if buf.strip():
            sents.append(buf.strip())
    return sents


def _is_consonant(ch):
    o = ord(ch)
    return (0x1000 <= o <= 0x1021) or (0x1023 <= o <= 0x1027) or o in (0x1029, 0x102A, 0x103F)


def _safe_boundary(tok, i):
    if i <= 0 or i >= len(tok):
        return False
    if not _is_consonant(tok[i]):
        return False
    if tok[i - 1] == "\u1039":
        return False
    nxt = tok[i + 1] if i + 1 < len(tok) else ""
    if nxt in ("\u103a", "\u1039"):
        return False
    return True


def split_long_token(tok, max_chars):
    parts = []
    start = 0
    n = len(tok)
    while n - start > max_chars:
        cut = None
        for i in range(start + max_chars, start, -1):
            if _safe_boundary(tok, i):
                cut = i
                break
        if cut is None:
            for i in range(start + max_chars + 1, n):
                if _safe_boundary(tok, i):
                    cut = i
                    break
        if cut is None:
            break
        parts.append(tok[start:cut])
        start = cut
    parts.append(tok[start:])
    return [p for p in parts if p]


def split_text_into_cues(text, per_cue, max_chars):
    cues = []
    cur = []

    def flush():
        if cur:
            cues.append(" ".join(cur))
            cur.clear()

    for tok in text.split():
        if len(tok) > max_chars:
            flush()
            cues.extend(split_long_token(tok, max_chars))
            continue
        if cur and (len(cur) >= per_cue or len(" ".join(cur + [tok])) > max_chars):
            flush()
        cur.append(tok)
        if tok.endswith(("၊", "။")):
            flush()
    flush()
    return cues


def build_srt_from_timeline(sentences, timeline, per_cue, max_chars):
    lines = []
    idx = 1
    for sent, (st_ms, en_ms) in zip(sentences, timeline):
        cues = split_text_into_cues(sent, per_cue, max_chars)
        if not cues:
            continue
        weights = [max(1, len(re.sub(r"\s", "", c))) for c in cues]
        total = sum(weights)
        span = en_ms - st_ms
        t = float(st_ms)
        for c, w in zip(cues, weights):
            d = span * w / total
            lines.append(f"{idx}\n{ms_to_srt_time(t)} --> {ms_to_srt_time(t + d)}\n{c}\n")
            t += d
            idx += 1
    return "\n".join(lines)


def trim_silence(seg, thresh=-50.0, keep_ms=40):
    from pydub.silence import detect_leading_silence
    lead = detect_leading_silence(seg, silence_threshold=thresh)
    tail = detect_leading_silence(seg.reverse(), silence_threshold=thresh)
    lead = max(0, lead - keep_ms)
    tail = max(0, tail - keep_ms)
    if len(seg) - lead - tail < 150:
        return seg
    return seg[lead:len(seg) - tail]


def enhance_loudness(seg, level):
    from pydub import effects
    if level == "off":
        return effects.normalize(seg, headroom=1.0)
    if level == "medium":
        seg = effects.compress_dynamic_range(seg, threshold=-18.0, ratio=1.8, attack=8.0, release=120.0)
    else:
        seg = effects.compress_dynamic_range(seg, threshold=-22.0, ratio=2.5, attack=8.0, release=120.0)
    return effects.normalize(seg, headroom=1.0)


def build_voiceover(mp3_list, gap_ms, loudness):
    combined = AudioSegment.empty()
    timeline = []
    cursor = 0
    for i, data in enumerate(mp3_list):
        seg = trim_silence(AudioSegment.from_file(io.BytesIO(data), format="mp3"))
        if i > 0 and gap_ms > 0:
            combined += AudioSegment.silent(duration=gap_ms, frame_rate=seg.frame_rate)
            cursor += gap_ms
        timeline.append((cursor, cursor + len(seg)))
        combined += seg
        cursor += len(seg)
    try:
        combined = enhance_loudness(combined, loudness)
    except Exception:
        pass
    buf = io.BytesIO()
    combined.export(buf, format="mp3", bitrate="96k")
    return buf.getvalue(), timeline


def fallback_voiceover(mp3_list):
    timeline = []
    cursor = 0.0
    for data in mp3_list:
        dur = MP3(io.BytesIO(data)).info.length * 1000
        timeline.append((cursor, cursor + dur))
        cursor += dur
    return b"".join(mp3_list), timeline


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


async def synth_one(sentence, voice, rate_str, pitch_str, sem, retries=3):
    async with sem:
        last = None
        for attempt in range(retries):
            try:
                comm = edge_tts.Communicate(sentence, voice, rate=rate_str, pitch=pitch_str)
                data = b""
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        data += chunk["data"]
                if data:
                    return data
            except Exception as e:
                last = e
            await asyncio.sleep(0.8 * (attempt + 1))
        raise RuntimeError(f"'{sentence[:25]}...' ထုတ်မရပါ ({last})")


async def synth_all(sentences, voice, rate_str, pitch_str, progress_cb=None, concurrency=4):
    sem = asyncio.Semaphore(concurrency)
    results = [None] * len(sentences)
    done = 0

    async def worker(i, s):
        nonlocal done
        results[i] = await synth_one(s, voice, rate_str, pitch_str, sem)
        done += 1
        if progress_cb:
            progress_cb(done, len(sentences))

    await asyncio.gather(*(worker(i, s) for i, s in enumerate(sentences)))
    return results


st.title("🎙️ မြန်မာ Voiceover Studio")
st.caption("Microsoft Edge TTS · အသံ + SRT ဖိုင် တစ်ခါတည်း ထုတ်ပေးသည်")

tab1, tab2 = st.tabs(["🗣️ Text → Voice + SRT", "🎧 Audio Upload → SRT"])

with tab1:
    text = st.text_area("📝 SCRIPT / စာသား", height=220, placeholder="သင့်စာသားကို ဒီနေရာမှာ ရိုက်ထည့်ပါ...", key="tts_text")

    voice_label = st.radio("🎭 VOICE ရွေးချယ်ရန်", list(VOICES.keys()))
    voice = VOICES[voice_label]

    c1, c2 = st.columns(2)
    with c1:
        rate = st.slider("⚡ RATE (အမြန်နှုန်း)", -50, 50, 0)
    with c2:
        pitch = st.slider("🎵 PITCH", -50, 50, 0)

    loudness_label = st.select_slider(
        "🔊 အသံကျယ်မှု",
        options=["ပုံမှန်", "ကျယ်", "အလွန်ကျယ်"],
        value="ကျယ်",
    )
    loudness = {"ပုံမှန်": "off", "ကျယ်": "medium", "အလွန်ကျယ်": "max"}[loudness_label]

    words_per_cue = st.slider("📝 SRT တစ်ကြောင်းလျှင် စာလုံးအရေအတွက်", 3, 15, 8)

    with st.expander("⚙️ အဆင့်မြင့် ချိန်ညှိချက်"):
        gap_ms = st.slider("စာကြောင်းတစ်ခုနဲ့တစ်ခုကြား အနားယူချိန် (ms)", 0, 800, 200, step=50)
        max_chars = st.slider("SRT တစ်ကြောင်း အများဆုံး စာလုံးရေ", 20, 100, 50)
        flat_newlines = st.checkbox("Enter (စာကြောင်းအဆင်း) များကို ဖယ်ပြီး ဆက်တိုက်ဖတ်မည်", value=True)
        soft_commas = st.checkbox("'၊' နေရာတွင် အနားမယူဘဲ ဆက်ဖတ်မည်", value=False)

    if "audio_bytes" not in st.session_state:
        st.session_state.audio_bytes = None
        st.session_state.srt_text = None

    if st.button("🎙️ Generate Voiceover", type="primary", use_container_width=True):
        clean_text = prepare_text(text, flat_newlines, soft_commas)
        sentences = split_sentences(clean_text)
        if not sentences:
            st.warning("စာသား ထည့်ပါ")
        else:
            progress = st.progress(0, text="အသံ ထုတ်နေသည်...")

            def cb(done, total):
                progress.progress(int(done / total * 90), text=f"စာကြောင်း {done}/{total} ထုတ်နေသည်...")

            try:
                mp3_list = asyncio.run(synth_all(sentences, voice, fmt(rate, "%"), fmt(pitch, "Hz"), cb))
            except Exception as e:
                mp3_list = None
                progress.empty()
                st.error(f"အသံထုတ်မရပါ — {e}")
            if mp3_list:
                progress.progress(95, text="အသံ ပေါင်းစပ်နေသည်...")
                try:
                    audio_bytes, timeline = build_voiceover(mp3_list, gap_ms, loudness)
                except Exception as e:
                    audio_bytes, timeline = fallback_voiceover(mp3_list)
                    st.info(f"Audio ပြင်ဆင်မှု မအောင်မြင်လို့ ရိုးရိုးပေါင်းစပ်ထားပါသည် ({e})")
                st.session_state.audio_bytes = audio_bytes
                st.session_state.srt_text = build_srt_from_timeline(sentences, timeline, words_per_cue, max_chars)
                progress.empty()

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
            st.text(st.session_state.srt_text)

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
            st.text(st.session_state.stt_srt)

st.caption("Myanmar Voiceover Studio · Edge TTS + Whisper STT · Unlimited Words")
