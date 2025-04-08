from flask import Flask, request, render_template, jsonify, url_for
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv
from pydub import AudioSegment
from rq import Queue
from rq.job import Job
import os
import re
import io
import time
import logging
from redis_config import get_redis_connection

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

# Ensure static directory exists
if not os.path.exists("static"):
    os.makedirs("static")

# Initialize Flask app
app = Flask(__name__, static_folder='static', static_url_path='/static')

# Initialize clients and queue
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
redis_conn = get_redis_connection()
q = Queue(connection=redis_conn)

def generate_audio_task(script):
    """Generate audio with ElevenLabs, add background music, and handle pauses."""
    # Generate audio from ElevenLabs
    audio_generator = elevenlabs_client.generate(
        text=script,
        voice="Rachel",
        model="eleven_monolingual_v1",
        voice_settings={"stability": 0.7, "similarity_boost": 0.75, "style": 0.2}
    )
    audio_bytes = b"".join(audio_generator)
    full_audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")

    # Load and adjust background music (12 minutes = 720,000 ms)
    bg_music_path = os.path.join(os.path.dirname(__file__), 'static', 'background_music.mp3')
    if not os.path.exists(bg_music_path):
        logging.error(f"Background music not found at {bg_music_path}")
        raise FileNotFoundError("Background music file missing")
    bg_music = AudioSegment.from_mp3(bg_music_path)
    bg_music = bg_music * int(720000 / len(bg_music)) + bg_music[:720000 % len(bg_music)]
    bg_music = bg_music - 20  # Reduce volume by 20dB

    # Split script by silence phrase and build audio
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
            final_audio += AudioSegment.silent(duration=30000)  # 30-second pause

    # Overlay background music and export
    final_audio = final_audio.overlay(bg_music, position=0)
    audio_path = os.path.join(os.path.dirname(__file__), 'static', 'meditation.mp3')
    final_audio.export(audio_path, format="mp3")
    
    # Log for debugging
    logging.info(f"Audio saved at: {audio_path}, exists: {os.path.exists(audio_path)}")
    
    # Return the URL for the frontend
    audio_url = url_for('static', filename='meditation.mp3', _external=True)
    return audio_url

def generate_meditation_script(situation):
    """Generate a meditation script using OpenAI."""
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

@app.route("/", methods=["GET", "POST"])
def index():
    """Handle GET for the form and POST to enqueue audio generation."""
    if request.method == "POST":
        situation = request.form.get("situation", "general anxiety")
        script = generate_meditation_script(situation)
        job = q.enqueue(generate_audio_task, script, job_timeout=600)  # 10-minute timeout
        return jsonify({"job_id": job.id})
    return render_template("index.html")

@app.route("/status/<job_id>")
def check_status(job_id):
    """Check the status of a job and return the audio URL when done."""
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        if job.is_failed:
            logging.error(f"Job {job_id} failed: {job.exc_info}")
            return jsonify({"status": "failed"})
        elif job.is_finished:
            audio_url = job.result
            return jsonify({"status": "done", "audio_url": audio_url})
        else:
            return jsonify({"status": "processing"})
    except Exception as e:
        logging.error(f"Error fetching job {job_id}: {str(e)}")
        return jsonify({"status": "error"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)