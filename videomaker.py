"""Video pipeline: script -> voiceover -> ken-burns slideshow -> captions -> MP4."""
import os
import random
import subprocess
import math

from PIL import Image, ImageDraw, ImageFont

# --- ffmpeg binary (portable) ---
try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = "ffmpeg"

W, H = 1080, 1920
FPS = 30

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

VOICES = {
    "Abeo — Nigerian male": "en-NG-AbeoNeural",
    "Ezinne — Nigerian female": "en-NG-EzinneNeural",
    "Ryan — British male": "en-GB-RyanNeural",
    "Sonia — British female": "en-GB-SoniaNeural",
}


def _find_font():
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            return p
    return None


def make_voiceover_edge(script, voice_id, out_mp3, proxy=None):
    """Edge TTS with real word-boundary timings. Returns [(start, end, word)]."""
    import asyncio
    import edge_tts

    async def _run():
        comm = edge_tts.Communicate(script, voice_id, proxy=proxy)
        words = []
        with open(out_mp3, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append((
                        chunk["offset"] / 10_000_000,
                        (chunk["offset"] + chunk["duration"]) / 10_000_000,
                        chunk["text"],
                    ))
        return words

    return asyncio.run(_run())


def make_voiceover_gtts(script, out_mp3):
    """gTTS fallback. Returns [(start, end, word)] with evenly spread timings."""
    from gtts import gTTS

    gTTS(script, lang="en", tld="com.ng").save(out_mp3)
    dur = get_duration(out_mp3)
    words = script.split()
    per = dur / max(len(words), 1)
    return [(i * per, (i + 1) * per, w) for i, w in enumerate(words)]


def make_voiceover(script, voice_id, out_mp3, proxy=None):
    """Try Edge TTS, fall back to gTTS. Returns word timings."""
    try:
        words = make_voiceover_edge(script, voice_id, out_mp3, proxy=proxy)
        if words and os.path.getsize(out_mp3) > 1000:
            return words
    except Exception as e:
        print("edge-tts failed, falling back to gTTS:", e)
    return make_voiceover_gtts(script, out_mp3)


def probe_duration(path):
    out = subprocess.run(
        [FFMPEG, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    # imageio-ffmpeg binary doesn't do ffprobe; use ffmpeg -i fallback
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def get_duration(path):
    """Duration via ffmpeg -i stderr parse (works with static binary)."""
    out = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            try:
                h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                return h * 3600 + m * 60 + s
            except Exception:
                pass
    return 0.0


def chunk_words(words, max_words=4):
    """Group word timings into caption chunks."""
    chunks = []
    cur = []
    for start, end, w in words:
        cur.append((start, end, w))
        if len(cur) >= max_words or w.rstrip().endswith((".", "!", "?", ",")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        text = " ".join(w for _, _, w in c)
        out.append((c[0][0], c[-1][1], text))
    return out


def render_caption_png(text, path, font_path):
    img = Image.new("RGBA", (W, 520), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, 72)
    # wrap to ~18 chars per line
    words = text.split()
    lines, line = [], ""
    for w in words:
        if len(line) + len(w) + 1 <= 18:
            line = (line + " " + w).strip()
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    y = 20
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font, stroke_width=4)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) / 2, y), ln, font=font, fill="white",
                  stroke_width=4, stroke_fill="black")
        y += (bbox[3] - bbox[1]) + 14
    img.save(path)


def kenburns_segment(image_path, duration, out_path, zoom_in=True):
    frames = max(int(duration * FPS), 1)
    if zoom_in:
        z_expr = f"1+0.12*on/{frames}"
    else:
        z_expr = f"1.12-0.12*on/{frames}"
    vf = (
        "scale=2160:3840:force_original_aspect_ratio=increase,"
        "crop=2160:3840,"
        f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={W}x{H}:fps={FPS}"
    )
    subprocess.run(
        [FFMPEG, "-y", "-i", image_path, "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", out_path],
        check=True, capture_output=True,
    )


def build_video(script, voice_id, workdir, progress=None, proxy=None):
    os.makedirs(workdir, exist_ok=True)
    audio_path = os.path.join(workdir, "voice.mp3")

    if progress:
        progress("🎙️ Making the voiceover...")
    words = make_voiceover(script, voice_id, audio_path, proxy=proxy)
    duration = get_duration(audio_path)
    if duration <= 0:
        raise RuntimeError("Voiceover came out empty — try again.")

    # --- visuals ---
    images = [os.path.join(ASSETS_DIR, f) for f in sorted(os.listdir(ASSETS_DIR))
              if f.lower().endswith((".webp", ".jpg", ".jpeg", ".png"))]
    if not images:
        raise RuntimeError("No visual assets found.")
    random.shuffle(images)
    seg_dur = 4.0
    n_segs = max(1, math.ceil(duration / seg_dur))
    if progress:
        progress(f"🎬 Building visuals ({n_segs} scenes)...")
    seg_paths = []
    t = 0.0
    for i in range(n_segs):
        d = min(seg_dur, duration - t)
        if d <= 0.3:
            break
        seg = os.path.join(workdir, f"seg{i:02d}.mp4")
        kenburns_segment(images[i % len(images)], d, seg, zoom_in=(i % 2 == 0))
        seg_paths.append(seg)
        t += d

    # concat
    list_path = os.path.join(workdir, "list.txt")
    with open(list_path, "w") as f:
        for s in seg_paths:
            f.write(f"file '{s}'\n")
    silent = os.path.join(workdir, "silent.mp4")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", silent],
        check=True, capture_output=True,
    )

    # --- captions ---
    if progress:
        progress("💬 Adding captions...")
    font_path = _find_font()
    chunks = chunk_words(words)
    overlay_inputs = []
    fc_parts = []
    prev = "0:v"
    for i, (cs, ce, text) in enumerate(chunks):
        cap = os.path.join(workdir, f"cap{i:02d}.png")
        if font_path:
            render_caption_png(text, cap, font_path)
        else:
            continue
        overlay_inputs += ["-i", cap]
        idx = i + 1
        out_label = f"[v{i}]" if i < len(chunks) - 1 else "[vout]"
        # keep text clear of the very bottom (phone UI zone)
        fc_parts.append(
            f"[{prev}][{idx}:v]overlay=0:1180:enable='between(t,{cs:.2f},{ce:.2f})'{out_label}"
        )
        prev = f"v{i}"

    final = os.path.join(workdir, "final.mp4")
    if overlay_inputs:
        fc = ";".join(fc_parts)
        num_caps = len(overlay_inputs) // 2
        cmd = ([FFMPEG, "-y", "-i", silent] + overlay_inputs +
               ["-i", audio_path, "-filter_complex", fc,
                "-map", "[vout]", "-map", f"{1 + num_caps}:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-c:a", "aac", "-shortest", final])
    else:
        cmd = ([FFMPEG, "-y", "-i", silent, "-i", audio_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-c:a", "aac", "-shortest", final])
    if progress:
        progress("✨ Assembling your video...")
    subprocess.run(cmd, check=True, capture_output=True)
    return final
