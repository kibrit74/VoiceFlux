import os
import json
from flask import Flask, render_template, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
from youtubesearchpython import VideosSearch

app = Flask(__name__)

def search_video_and_get_transcript(query):
    # Video araması yap ve ilk videonun ID'sini al
    videos_search = VideosSearch(query, limit=1)
    results = videos_search.result()
    
    if not results['result']:
        return None, None, "Video bulunamadı"
    
    video_id = results['result'][0]['id']
    
    # Önce otomatik oluşturulan Türkçe transkripti almayı dene
    transcript, error = get_transcript(video_id, ['tr'])
    
    # Eğer Türkçe transkript alınamazsa, İngilizce transkripti dene
    if not transcript:
        transcript, error = get_transcript(video_id, ['en'])
    
    # Hala transkript alınamadıysa, mevcut herhangi bir dili dene
    if not transcript:
        transcript, error = get_available_transcript(video_id)
    
    return video_id, transcript, error

def get_transcript(video_id, language_codes):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=language_codes)
        return transcript, None
    except Exception as e:
        return None, str(e)

def get_available_transcript(video_id):
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        for transcript in transcript_list:
            return transcript.fetch(), None
    except Exception as e:
        return None, f"Hiçbir dilde transkript bulunamadı: {str(e)}"

@app.route('/')
def index():
    return render_template('index11.html')

@app.route('/play')
def play():
    query = request.args.get('query')
    video_id, _, error = search_video_and_get_transcript(query)
    if video_id:
        return render_template('play.html', video_id=video_id)
    return error, 400

@app.route('/transcript', methods=['POST'])
def transcript():
    data = request.json
    query = data.get('query')
    
    if not query:
        return jsonify({'error': 'Arama terimi eksik'}), 400

    video_id, transcript, error = search_video_and_get_transcript(query)
    
    if error:
        return jsonify({'error': error}), 500
    
    if transcript:
        output_file = f"{video_id}.json"
        output_dir = os.path.join('static', 'transcripts')
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, output_file)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(transcript, f, ensure_ascii=False, indent=4)
        
        print(f"Transkript {file_path} olarak kaydedildi.")
        return jsonify({'transcript_file': f"transcripts/{output_file}", 'video_id': video_id})
    
    return jsonify({'error': 'Transkript alınamadı'}), 500

if __name__ == "__main__":
    app.run(debug=True)