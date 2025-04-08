from flask import Flask, request, render_template, jsonify, url_for, send_file
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from pydub import AudioSegment
from rq import Queue
from rq.job import Job
import rq  # Add this import
import os
import re
import io
import time
import logging
from redis_config import get_redis_connection

# Configure logging
logging.basicConfig(level=logging.INFO)

load_dotenv()
if not os.path.exists("static"):
    os.makedirs("static")

app = Flask(__name__, static_folder='static', static_url_path='/static')
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
redis_conn = get_redis_connection()
q = Queue(connection=redis_conn)

def generate_audio_task(script):
    audio_generator = elevenlabs_client.generate(
        text=script,
        voice="Rachel",
        model="eleven_monolingual_v1",
        voice_settings={"stability": 0.7, "similarity_boost": 0.75, "style": 0.2}
    )
    audio_bytes = b"".join(audio_generator)
    full_audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")

    bg_music = AudioSegment.from_mp3("static/background_music.mp3")
    bg_music = bg_music * int(720000 / len(bg_music)) + bg_music[:720000 % len(bg_music)]
    bg_music = bg_music - 30

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
    audio_buffer = io.BytesIO()
    final_audio.export(audio_buffer, format="mp3")
    audio_data = audio_buffer.getvalue()
    audio_buffer.close()
    job_id = rq.get_current_job().id  # Now works with rq imported
    redis_conn.setex(f"audio:{job_id}", 3600, audio_data)  # Store for 1 hour
    logging.info(f"Audio stored in Redis for job {job_id}")
    return job_id  # Return job ID to fetch audio later

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        situation = request.form["situation"]
        script = generate_meditation_script(situation)
        job = q.enqueue(generate_audio_task, script, job_timeout=600)
        return jsonify({"job_id": job.id})
    return render_template("index.html")

@app.route("/status/<job_id>")
def check_status(job_id):
    job = Job.fetch(job_id, connection=redis_conn)
    if job is None or job.is_failed:
        return jsonify({"status": "failed"})
    elif job.is_finished:
        audio_url = url_for('get_audio', job_id=job_id, _external=True)
        return jsonify({"status": "done", "audio_url": audio_url})
    else:
        return jsonify({"status": "processing"})

@app.route("/audio/<job_id>")
def get_audio(job_id):
    audio_data = redis_conn.get(f"audio:{job_id}")
    if audio_data is None:
        return "Audio not found", 404
    return send_file(
        io.BytesIO(audio_data),
        mimetype="audio/mp3",
        as_attachment=False,
        download_name="meditation.mp3"
    )

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)