from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask_cors import CORS
import requests
import time
import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import pytz
import threading

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'flac', 'm4a', 'mp4', 'wma', 'aac', 'opus', '3gp'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Firebase Initialization
cred = credentials.Certificate("serviceAccountKey.json")  # Update with your Firebase service account JSON
firebase_admin.initialize_app(cred)
db = firestore.client()

# AssemblyAI Config
ASSEMBLYAI_API_KEY = 'a0ad2c9f9ef440fe94dd52fa2c3db134'
ASSEMBLYAI_URL = 'https://api.assemblyai.com/v2'
headers = {'authorization': ASSEMBLYAI_API_KEY}

def allowed_file(filename):
    """Check if the uploaded file has a valid audio extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_ist_timestamp():
    """Returns the current date and time in IST (Indian Standard Time)."""
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime('%Y-%m-%d %H:%M:%S')

def transcribe_audio(file_path):
    """Uploads audio and gets transcription from AssemblyAI."""
    try:
        with open(file_path, 'rb') as f:
            response = requests.post(f'{ASSEMBLYAI_URL}/upload', headers=headers, files={'file': f})
        
        if response.status_code != 200:
            return {"error": "File upload failed", "details": response.json()}

        audio_url = response.json().get('upload_url')

        json_data = {'audio_url': audio_url, 'speaker_labels': True}
        transcript_response = requests.post(f'{ASSEMBLYAI_URL}/transcript', json=json_data, headers=headers)

        if transcript_response.status_code != 200:
            return {"error": "Transcription request failed", "details": transcript_response.json()}

        transcript_id = transcript_response.json().get('id')
        polling_url = f'{ASSEMBLYAI_URL}/transcript/{transcript_id}'

        while True:
            polling_response = requests.get(polling_url, headers=headers)
            status = polling_response.json().get('status')

            if status == 'completed':
                return polling_response.json()
            elif status == 'failed':
                return {"error": "Transcription failed"}

            time.sleep(5)
    except Exception as e:
        return {"error": str(e)}


def process_transcription(file_path, user_id, file_name):
    """Handles transcription and Firestore update in a background thread."""
    transcription_response = transcribe_audio(file_path)

    if isinstance(transcription_response, dict) and "error" in transcription_response:
        print(f"Transcription failed for {file_name}: {transcription_response['error']}")
        return

    speaker_map = {}
    speaker_counter = 0
    formatted_transcription = ""

    for utterance in transcription_response.get('utterances', []):
        speaker = utterance['speaker']
        if speaker not in speaker_map:
            speaker_map[speaker] = chr(65 + speaker_counter)  # Assign A, B, C...
            speaker_counter += 1

        topic = "TRANSCRIPTION OF AUDIO"
        formatted_transcription += f"Speaker {speaker_map[speaker]}: {utterance['text']}\n\n"

    server_timestamp = get_ist_timestamp()
    file_metadata = {
        "Response": topic + "\n\n" + formatted_transcription.strip(),
        "status":"processed",
        "Server_Timestamp": server_timestamp,
        "Notification": "On"
    }

    try:
        db.collection("ProcessedDocs").document(user_id).collection("UserFiles").document(file_name).set(file_metadata, merge=True)
        print(f"Transcription saved successfully for {file_name}")
    except Exception as e:
        print(f"Failed to save transcription for {file_name}: {str(e)}")

@app.route('/upload', methods=['POST'])
def upload_audio():
    """Handles audio file upload and starts transcription in the background."""
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    user_id = request.form.get('userId')
    file_name = request.form.get('fileName')

    if not user_id or not file_name:
        return jsonify({"error": "Missing userId or fileName"}), 400

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Invalid file format"}), 400

    # Secure filename and save locally
    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    # Start transcription in a separate thread
    threading.Thread(target=process_transcription, args=(file_path, user_id, file_name)).start()

    return jsonify({"message": "File uploaded successfully. Transcription is processing in the background."}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)