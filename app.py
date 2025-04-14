from flask import Flask, request, render_template, jsonify, url_for, send_file, redirect, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI
from elevenlabs import ElevenLabs, VoiceSettings
from dotenv import load_dotenv
import sqlite3
import os
import time
import logging
import uuid
from pydub import AudioSegment
import re
from datetime import datetime
import stripe

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('app.log')]
)
logger = logging.getLogger(__name__)

load_dotenv()
if not os.path.exists("static/audio"):
    os.makedirs("static/audio")

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.urandom(24)
app.config['REMEMBER_COOKIE_DURATION'] = 604800  # 7 days

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
if not os.getenv("OPENAI_API_KEY"):
    logger.error("OPENAI_API_KEY not set in .env")
    raise ValueError("Please set OPENAI_API_KEY in .env file")

# Initialize ElevenLabs client
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
if not os.getenv("ELEVENLABS_API_KEY"):
    logger.error("ELEVENLABS_API_KEY not set in .env")
    raise ValueError("Please set ELEVENLABS_API_KEY in .env file")

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
stripe_publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY")
if not os.getenv("STRIPE_SECRET_KEY"):
    logger.error("STRIPE_SECRET_KEY not set in .env")
    raise ValueError("Please set STRIPE_SECRET_KEY in .env file")
if not os.getenv("STRIPE_PUBLISHABLE_KEY"):
    logger.error("STRIPE_PUBLISHABLE_KEY not set in .env")
    raise ValueError("Please set STRIPE_PUBLISHABLE_KEY in .env file")

# SQLite setup
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT, credits INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (id TEXT PRIMARY KEY, user_id TEXT, job_id TEXT, file_path TEXT, situation TEXT, created_at TIMESTAMP,
                  FOREIGN KEY(user_id) REFERENCES users(id))''')
    conn.commit()
    conn.close()

init_db()

# User model
class User(UserMixin):
    def __init__(self, id, email, credits):
        self.id = id
        self.email = email
        self.credits = credits

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT id, email, credits FROM users WHERE id = ?', (user_id,))
    user_data = c.fetchone()
    conn.close()
    if user_data:
        return User(user_data[0], user_data[1], user_data[2])
    return None

def clean_text(text):
    """Remove special characters that may cause issues."""
    replacements = {
        '…': '...',
        '’': "'",
        '“': '"',
        '”': '"',
        '—': '-',
        '**': '',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'[^\w\s.,!?\'"-]', '', text)
    return text

def generate_meditation_script(situation):
    logger.info(f"Generating meditation script for situation: {situation}")
    start_time = time.time()

    try:
        prompt = (
            f"Create a calming meditation script addressing '{situation}'. "
            "The script should be approximately 5 minutes long (600-750 words) when read at a soothing pace. "
            "Include 3-4 explicit pauses marked as '[PAUSE 20 SECONDS]' for silent reflection. "
            "Keep it soothing, structured with clear breathing instructions, and use a warm, empathetic tone to ease the user's anxiety. "
            "Avoid special characters like curly quotes, em dashes, or asterisks."
        )
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a meditation guide crafting personalized, calming scripts."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        script = response.choices[0].message.content.strip()
        script = clean_text(script)
        logger.info(f"GPT-4o script generated in {time.time() - start_time:.2f} seconds")
        return script
    except Exception as e:
        logger.error(f"Script generation failed: {str(e)}")
        script = f"""
        Welcome to your meditation. Find a comfortable position and close your eyes. 
        Take a deep breath in, and exhale slowly, letting tension slip away. [PAUSE 20 SECONDS]
        Imagine a serene lake, its surface calm and still. As you breathe in, feel your worries about {situation} soften. 
        Exhale, releasing them into the water. Let your shoulders relax, your mind ease. 
        Picture yourself sitting by this lake, the air cool and gentle. Each breath brings calm deeper into your body. [PAUSE 20 SECONDS]
        Now, visualize a quiet forest path. Each step grounds you, each breath calms you. 
        Notice the soft sunlight filtering through the trees, warming your face. Feel your anxiety easing, replaced by peace. 
        You are safe here, held by the earth beneath you. Let your breath flow naturally, slow and steady. [PAUSE 20 SECONDS]
        Picture a gentle stream, its flow carrying away any remaining stress. 
        Inhale deeply, filling your lungs with calm. Exhale, letting go completely. 
        Feel your body light, your mind clear. You are present, at ease, whole. [PAUSE 20 SECONDS]
        As we close, carry this tranquility with you, knowing you can return here anytime. 
        Take one final deep breath, and when you are ready, gently open your eyes.
        """
        script = clean_text(script)
        logger.info("Using fallback static script")
        return script

def generate_audio(script, job_id, user_id, situation):
    logger.info(f"Starting audio generation for job {job_id}, user {user_id}")
    start_time = time.time()

    try:
        # Split script by pauses, ensuring no empty segments
        segments = [s.strip() for s in script.split("[PAUSE 20 SECONDS]") if s.strip()]
        if not segments:
            raise Exception("No valid segments found in script")
        logger.info(f"Found {len(segments)} script segments")

        audio_path = f"static/audio/audio_{user_id}_{job_id}.mp3"
        temp_silence = f"static/audio/silence_{job_id}.mp3"

        # Generate 20-second silence
        silence = AudioSegment.silent(duration=20000)  # 20 seconds in milliseconds
        silence.export(temp_silence, format="mp3")

        # Generate audio for each segment
        temp_files = []
        for i, segment in enumerate(segments):
            segment_path = f"static/audio/segment_{job_id}_{i}.mp3"
            try:
                logger.info(f"Generating audio for segment {i}: {segment[:50]}...")
                cleaned_segment = segment.replace("[PAUSE 20 SECONDS]", "").strip()
                if not cleaned_segment:
                    logger.warning(f"Segment {i} is empty after cleaning, skipping")
                    continue
                audio_stream = elevenlabs_client.generate(
                    text=cleaned_segment,
                    voice="Rachel",
                    model="eleven_monolingual_v1",
                    voice_settings=VoiceSettings(
                        stability=0.5,
                        similarity_boost=0.5
                    )
                )
                # Save audio stream to file
                with open(segment_path, "wb") as f:
                    for chunk in audio_stream:
                        if chunk:
                            f.write(chunk)
                # Verify the generated file
                audio_segment = AudioSegment.from_mp3(segment_path)
                if audio_segment.duration_seconds > 0:
                    temp_files.append(segment_path)
                    logger.info(f"Segment {i} duration: {audio_segment.duration_seconds:.2f} seconds")
                else:
                    logger.warning(f"Skipping empty segment {i} for job {job_id}")
            except Exception as e:
                logger.error(f"Failed to generate segment {i} for job {job_id}: {str(e)}")
                continue
            # Add silence after each segment (except the last)
            if i < len(segments) - 1:
                temp_files.append(temp_silence)
                logger.info(f"Added 20-second silence after segment {i}")

        if not temp_files:
            raise Exception("No valid audio segments or silence generated")

        # Concatenate audio files
        combined = AudioSegment.empty()
        total_duration = 0
        for file in temp_files:
            try:
                segment = AudioSegment.from_mp3(file)
                combined += segment
                total_duration += segment.duration_seconds
                logger.info(f"Added {file} to combined audio, duration now: {total_duration:.2f} seconds")
            except Exception as e:
                logger.error(f"Failed to process file {file}: {str(e)}")
                continue
        if total_duration < 60:  # Ensure at least 1 minute
            raise Exception(f"Generated audio too short: {total_duration:.2f} seconds")
        combined.export(audio_path, format="mp3")
        logger.info(f"Final audio exported to {audio_path}")

        # Clean up temporary files (keep final audio)
        for file in temp_files:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as e:
                    logger.error(f"Failed to delete temp file {file}: {str(e)}")

        # Save file metadata to database
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        file_id = str(uuid.uuid4())
        created_at = datetime.utcnow()
        c.execute(
            "INSERT INTO files (id, user_id, job_id, file_path, situation, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, user_id, job_id, audio_path, situation, created_at)
        )
        conn.commit()
        conn.close()

        total_time = time.time() - start_time
        logger.info(f"Audio generation completed for job {job_id} in {total_time:.2f} seconds, total duration: {total_duration:.2f} seconds")
        return audio_path
    except Exception as e:
        logger.error(f"Audio generation failed for job {job_id}: {str(e)}")
        # Clean up any remaining temp files
        for file in [f"static/audio/segment_{job_id}_{i}.mp3" for i in range(len(segments))] + [temp_silence]:
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as e:
                    logger.error(f"Failed to delete temp file {file}: {str(e)}")
        raise

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        logger.info(f"Signup attempt for email: {email}")
        try:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("SELECT email FROM users WHERE email = ?", (email,))
            if c.fetchone():
                flash("Email already exists.")
                conn.close()
                return render_template("signup.html")
            user_id = str(uuid.uuid4())
            hashed_password = generate_password_hash(password)
            c.execute("INSERT INTO users (id, email, password, credits) VALUES (?, ?, ?, ?)",
                     (user_id, email, hashed_password, 2))
            conn.commit()
            conn.close()
            user = User(user_id, email, 2)
            login_user(user, remember=True)
            logger.info(f"User {email} signed up with 2 credits")
            flash("Signup successful! You have 2 credits.")
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"Signup failed: {str(e)}")
            flash("An error occurred during signup.")
            return render_template("signup.html")
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        logger.info(f"Login attempt for email: {email}")
        try:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("SELECT id, email, password, credits FROM users WHERE email = ?", (email,))
            user_data = c.fetchone()
            conn.close()
            if user_data and check_password_hash(user_data[2], password):
                user = User(user_data[0], user_data[1], user_data[3])
                login_user(user, remember=True)
                logger.info(f"User {email} logged in")
                flash("Login successful!")
                return redirect(url_for('index'))
            flash("Invalid email or password.")
            return render_template("login.html")
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            flash("An error occurred during login.")
            return render_template("login.html")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logger.info(f"User {current_user.email} logging out")
    logout_user()
    flash("Logged out successfully.")
    return redirect(url_for('index'))

@app.route("/", methods=["GET", "POST"])
def index():
    logger.info("Received request to /")
    saved_files = []
    if current_user.is_authenticated:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT job_id, file_path, situation, created_at FROM files WHERE user_id = ? ORDER BY created_at DESC",
                 (current_user.id,))
        saved_files = [
            {
                "job_id": row[0],
                "audio_url": url_for('get_audio', job_id=row[0], _external=True),
                "situation": row[2],
                "created_at": row[3]
            } for row in c.fetchall()
        ]
        conn.close()

    if request.method == "POST":
        if not current_user.is_authenticated:
            session['situation'] = request.form.get("situation")
            logger.info("User not logged in, redirecting to login")
            return jsonify({"redirect": url_for('login')})
        
        if current_user.credits < 1:
            logger.info(f"User {current_user.email} has no credits, redirecting to payments")
            return jsonify({"redirect": url_for('payments')})

        situation = request.form.get("situation")
        logger.info(f"Processing POST request with situation: {situation}")
        start_time = time.time()

        try:
            # Deduct credit first
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute("UPDATE users SET credits = credits - 1 WHERE id = ?", (current_user.id,))
            conn.commit()
            c.execute("SELECT credits FROM users WHERE id = ?", (current_user.id,))
            current_user.credits = c.fetchone()[0]
            conn.close()

            script = generate_meditation_script(situation)
            job_id = str(uuid.uuid4())
            logger.info(f"Generated job ID: {job_id}")
            logger.info("Generating audio synchronously")
            audio_path = generate_audio(script, job_id, current_user.id, situation)
            audio_url = url_for('get_audio', job_id=job_id, _external=True)
            
            logger.info(f"Audio generated at {audio_path} in {time.time() - start_time:.2f} seconds")
            logger.info(f"User {current_user.email} credits updated to {current_user.credits}")
            return jsonify({"job_id": job_id, "script": script, "audio_url": audio_url, "credits": current_user.credits})
        except Exception as e:
            logger.error(f"POST request failed: {str(e)}")
            return jsonify({"error": str(e)}), 500

    situation = session.pop('situation', None)
    logger.info("Rendering index.html for GET request")
    return render_template("index.html", situation=situation, credits=getattr(current_user, 'credits', 0), saved_files=saved_files)

@app.route("/audio/<job_id>")
@login_required
def get_audio(job_id):
    logger.info(f"Fetching audio for job {job_id}")
    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT file_path FROM files WHERE job_id = ? AND user_id = ?", (job_id, current_user.id))
        result = c.fetchone()
        conn.close()
        if not result:
            logger.warning(f"Audio not found for job {job_id}")
            return "Audio not found", 404
        audio_path = result[0]
        if not os.path.exists(audio_path):
            logger.warning(f"Audio file missing for job {job_id} at {audio_path}")
            return "Audio file missing", 404
        logger.info(f"Sending audio for job {job_id}")
        return send_file(
            audio_path,
            mimetype="audio/mp3",
            as_attachment=False,
            download_name=f"meditation_{job_id}.mp3"
        )
    except Exception as e:
        logger.error(f"Failed to fetch audio for job {job_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/get_script/<job_id>")
@login_required
def get_script(job_id):
    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("SELECT situation FROM files WHERE job_id = ? AND user_id = ?", (job_id, current_user.id))
        situation = c.fetchone()
        if not situation:
            return jsonify({"error": "File not found"}), 404
        # Regenerate script deterministically
        script = generate_meditation_script(situation[0])
        conn.close()
        return jsonify({"script": script})
    except Exception as e:
        logger.error(f"Failed to fetch script for job {job_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/payments")
@login_required
def payments():
    logger.info(f"User {current_user.email} accessing payments page")
    return render_template("payments.html", stripe_publishable_key=stripe_publishable_key)

@app.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[
                {
                    'price': 'price_1RDhr9KjJ23rv2vUuUVMw7jB',  # Replace with your actual Price ID
                    'quantity': 1,
                },
            ],
            metadata={'user_id': current_user.id},
            mode='payment',
            success_url=url_for('success', _external=True),
            cancel_url=url_for('cancel', _external=True),
        )
        logger.info(f"Checkout session created for user {current_user.email}: {checkout_session.id}")
        # Update credits immediately (no webhook)
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET credits = credits + 10 WHERE id = ?", (current_user.id,))
        conn.commit()
        conn.close()
        logger.info(f"Added 10 credits to user {current_user.id}")
        return jsonify({'id': checkout_session.id})
    except Exception as e:
        logger.error(f"Failed to create checkout session for user {current_user.email}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route("/success")
@login_required
def success():
    flash("Payment successful! 10 credits added to your account.")
    return redirect(url_for('index'))

@app.route("/cancel")
@login_required
def cancel():
    flash("Payment cancelled. No credits were added.")
    return redirect(url_for('index'))

@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, stripe_webhook_secret
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {str(e)}")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {str(e)}")
        return jsonify({'error': 'Invalid signature'}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        user_id = session.get('metadata', {}).get('user_id')
        if user_id:
            try:
                conn = sqlite3.connect('users.db')
                c = conn.cursor()
                c.execute("UPDATE users SET credits = credits + 10 WHERE id = ?", (user_id,))
                conn.commit()
                conn.close()
                logger.info(f"Added 10 credits to user {user_id} via webhook")
            except Exception as e:
                logger.error(f"Failed to update credits for user {user_id}: {str(e)}")
                return jsonify({'error': str(e)}), 500

    return jsonify({'status': 'success'}), 200

if __name__ == "__main__":
    logger.info("Starting Flask application")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)