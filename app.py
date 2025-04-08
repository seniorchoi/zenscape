from flask import Flask, request, render_template, jsonify
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from pydub import AudioSegment
import os
import re
import io

load_dotenv()
if not os.path.exists("static"):
    os.makedirs("static")

app = Flask(__name__)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            situation = request.form["situation"]
            script = generate_meditation_script(situation)
            audio_path = generate_audio(script)
            audio_url = f"/static/{os.path.basename(audio_path)}"
            return jsonify({"audio_url": audio_url})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return render_template("index.html")

def generate_meditation_script(situation):
    prompt = f"""
    Create a 10-minute guided meditation script for someone feeling anxious about '{situation}'. 
    Keep it calm, positive, and soothing. Include a short intro, breathing exercises, visualization, 
    and a gentle closing. Aim for about 800-1000 words (roughly 10 minutes when spoken). 
    Naturally incorporate the exact phrase 'now, take a moment of silence' here and there throughout 
    the script to indicate pauses, using it at least 3-5 times in appropriate spots. 
    Do not use Markdown, asterisks (*), bullet points, or any special formatting characters—just plain text.
    """
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.7
    )
    script = response.choices[0].message.content
    script = re.sub(r"\*+", "", script)
    return script

def generate_audio(script):
    # Generate full audio in one go
    audio_generator = elevenlabs_client.generate(
        text=script,
        voice="Rachel",
        model="eleven_monolingual_v1",
        voice_settings={"stability": 0.7, "similarity_boost": 0.75, "style": 0.2}
    )
    audio_bytes = b"".join(audio_generator)
    full_audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")

    # Load and loop background music
    bg_music = AudioSegment.from_mp3("static/background_music.mp3")
    bg_music = bg_music * int(720000 / len(bg_music)) + bg_music[:720000 % len(bg_music)]
    bg_music = bg_music - 30

    # Split at silence markers and insert 30s silence
    parts = re.split(r"(now, take a moment of silence)", script, flags=re.IGNORECASE)
    final_audio = AudioSegment.empty()
    current_pos = 0

    for i, part in enumerate(parts):
        part = part.strip()
        if part and not part.lower() == "now, take a moment of silence":
            part_duration = len(full_audio[current_pos:]) if i == len(parts) - 1 else \
                            len(full_audio[current_pos:]) - sum(len(p) for p in parts[i+1:])
            part_audio = full_audio[current_pos:current_pos + part_duration]
            final_audio += part_audio
            current_pos += part_duration
        if i < len(parts) - 1 and part:
            final_audio += AudioSegment.silent(duration=30000)

    final_audio = final_audio.overlay(bg_music, position=0)
    audio_path = "static/meditation.mp3"
    final_audio.export(audio_path, format="mp3")
    return audio_path

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)