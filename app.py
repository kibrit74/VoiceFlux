from flask import Flask, render_template, request, jsonify
import google.generativeai as genai
import yt_dlp
import os
from gtts import gTTS
import time
from werkzeug.serving import run_simple

app = Flask(__name__)
app.config['TIMEOUT'] = 300  # 5 dakika

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

def summarize_with_gemini(text, target_language, max_retries=3):
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"Translate the following English text to {target_language}, focusing only on the spoken content. Provide a meaningful and coherent translation without using special characters like # or *:\n\n{text}"
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"Gemini işleme hatası (Deneme {attempt + 1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(5)  # 5 saniye bekle ve tekrar dene
            else:
                return None

def text_to_speech(text, output_file):
    try:
        tts = gTTS(text=text, lang='tr')
        tts.save(output_file)
        return output_file
    except Exception as e:
        print(f"Ses dosyası oluşturma hatası: {str(e)}")
        return None

@app.route('/')
def index():
    return render_template('index111.html')

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    
    transcript = download_transcript(video_url)
    if not transcript:
        return jsonify({"error": "Transkript indirilemedi."}), 400

    summary = summarize_with_gemini(transcript, target_language)
    if not summary:
        return jsonify({"error": "Özet oluşturulamadı."}), 400

    audio_file = f'static/audio/summary_{os.urandom(16).hex()}.mp3'
    if not text_to_speech(summary, audio_file):
        return jsonify({"error": "Sesli özet oluşturulamadı."}), 400

    video_id = video_url.split('v=')[1]
    return jsonify({
        "video_id": video_id,
        "audio_file": audio_file,
        "summary": summary
    })

@app.route('/result')
def result():
    video_id = request.args.get('video_id')
    audio_file = request.args.get('audio_file')
    target_language = request.args.get('target_language')
    return render_template('result.html', video_id=video_id, audio_file=audio_file, target_language=target_language)

if __name__ == '__main__':
    if not os.path.exists('static/audio'):
        os.makedirs('static/audio')
    run_simple('localhost', 5000, app, use_reloader=True, use_debugger=True, threaded=True)
