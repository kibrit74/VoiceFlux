# app.py
from flask import Flask, render_template, request, jsonify
import google.generativeai as genai
import yt_dlp
import os
from gtts import gTTS
from celery import Celery
from flask_caching import Cache

app = Flask(__name__)
app.config['CACHE_TYPE'] = 'simple'
cache = Cache(app)

# Celery yapılandırması
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Sabit değer
GEMINI_API_KEY = "AIzaSyCocxsiyfBAOQXzInSoJU70M5PDzDmJA38"  # Gerçek API anahtarınızı buraya yazın

def download_transcript(url):
    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'skip_download': True,
        'outtmpl': 'subtitle',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'subtitles' in info and 'en' in info['subtitles']:
                subtitle_url = info['subtitles']['en'][0]['url']
                ydl.download([subtitle_url])
                with open('subtitle.en.vtt', 'r', encoding='utf-8') as f:
                    content = f.read()
                os.remove('subtitle.en.vtt')
                return clean_transcript(content)
            elif 'automatic_captions' in info and 'en' in info['automatic_captions']:
                subtitle_url = info['automatic_captions']['en'][0]['url']
                ydl.download([subtitle_url])
                with open('subtitle.en.vtt', 'r', encoding='utf-8') as f:
                    content = f.read()
                os.remove('subtitle.en.vtt')
                return clean_transcript(content)
            else:
                return None
    except Exception as e:
        print(f"Transkript indirme hatası: {str(e)}")
        return None

def clean_transcript(content):
    lines = content.split('\n')
    cleaned_lines = []
    for line in lines:
        if '-->' not in line and not line.strip().isdigit() and line.strip() != 'WEBVTT':
            cleaned_lines.append(line.strip())
    return ' '.join(cleaned_lines)

def summarize_with_gemini(text, target_language):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"Translate the following English text to {target_language}, focusing only on the spoken content. Provide a meaningful and coherent translation without using special characters like # or *:\n\n{text}"
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini işleme hatası: {str(e)}")
        return None

def text_to_speech(text, output_file):
    try:
        tts = gTTS(text=text, lang='tr')
        tts.save(output_file)
        return output_file
    except Exception as e:
        print(f"Ses dosyası oluşturma hatası: {str(e)}")
        return None

@celery.task
def process_video(video_url, target_language):
    transcript = download_transcript(video_url)
    if not transcript:
        return {"error": "Transkript indirilemedi."}

    summary = summarize_with_gemini(transcript, target_language)
    if not summary:
        return {"error": "Özet oluşturulamadı."}

    audio_file = f'static/audio/summary_{os.urandom(16).hex()}.mp3'
    if not text_to_speech(summary, audio_file):
        return {"error": "Sesli özet oluşturulamadı."}

    video_id = video_url.split('v=')[1]
    return {
        "video_id": video_id,
        "audio_file": audio_file,
        "summary": summary
    }

@app.route('/')
def index():
    return render_template('index111.html')

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    
    task = process_video.delay(video_url, target_language)
    return jsonify({"task_id": task.id}), 202

@app.route('/status/<task_id>')
def task_status(task_id):
    task = process_video.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Görev işleme alındı...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', '')
        }
        if 'result' in task.info:
            response['result'] = task.info['result']
    else:
        response = {
            'state': task.state,
            'status': str(task.info)
        }
    return jsonify(response)

if __name__ == '__main__':
    app.run(debug=False)
