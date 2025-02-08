from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask_cors import CORS
import requests
import time
import os
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)
CORS(app)

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'wav', 'mp3', 'ogg', 'flac', 'm4a'}

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

@app.route('/upload', methods=['POST'])
def upload_audio():
    """Handles audio file upload, transcribes it, and saves text response directly in Firestore."""
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

    # Transcribe the audio
    transcription_response = transcribe_audio(file_path)

    if isinstance(transcription_response, dict) and "error" in transcription_response:
        return jsonify(transcription_response), 500

    # Extract transcription as plain text
    transcription_text = "\n".join([f"Speaker {u['speaker']}: {u['text']}" for u in transcription_response.get('utterances', [])])

    # Save metadata in Firestore
    file_metadata = {
        "Response": transcription_text  # Storing as plain text
    }

    try:
        db.collection("ProcessedDocs").document(user_id).collection("UserFiles").document(file_name).update(file_metadata)
        return jsonify({"message": "File uploaded and transcribed successfully", "data": file_metadata})
    except Exception as e:
        return jsonify({"error": f"Failed to save to Firestore: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
