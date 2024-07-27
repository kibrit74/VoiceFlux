import os
import re
import json
import time
import logging
import urllib.request
import urllib.parse
from flask import Flask, render_template, request, jsonify, send_file
from gtts import gTTS
import google.generativeai as genai
from functools import lru_cache
from urllib.error import HTTPError

app = Flask(__name__, static_folder='static', static_url_path='/static')

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

genai.configure(api_key="AIzaSyBWC2gp-UjhlnGQgM0S77lftXnSl0uhqQ0")

TEMP_AUDIO_DIR = os.path.join(app.root_path, 'static', 'temp_audio')
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# İstek hızı sınırlama için değişkenler
RATE_LIMIT = 1  # saniyede maksimum istek sayısı
last_request_time = 0

def extract_video_id(url):
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
        r'(?:youtube\.com\/embed\/)([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url if len(url) == 11 else None

@lru_cache(maxsize=100)
def cached_get_youtube_transcript(video_id):
    return _get_youtube_transcript(video_id)

def rate_limited_get_youtube_transcript(video_id):
    global last_request_time
    current_time = time.time()
    time_since_last_request = current_time - last_request_time
    
    if time_since_last_request < 1 / RATE_LIMIT:
        time.sleep(1 / RATE_LIMIT - time_since_last_request)
    
    last_request_time = time.time()
    return cached_get_youtube_transcript(video_id)

def _get_youtube_transcript(video_id):
    max_retries = 3
    retry_delay = 5  # saniye

    for attempt in range(max_retries):
        try:
            url = f"https://www.youtube.com/watch?v={video_id}"
            html = urllib.request.urlopen(url).read().decode('utf-8')
            
            data_match = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?})\s*;\s*</script>', html)
            if not data_match:
                return None
            
            data = json.loads(data_match.group(1))
            captions = data['captions']['playerCaptionsTracklistRenderer']['captionTracks']
            
            if not captions:
                return None
            
            caption_url = captions[0]['baseUrl']
            caption_data = urllib.request.urlopen(caption_url).read().decode('utf-8')
            
            transcript = []
            for line in caption_data.split('\n'):
                if re.match(r'\d+:\d+:\d+\.\d+,\d+:\d+:\d+\.\d+', line):
                    continue
                if line.strip():
                    transcript.append(line.strip())
            
            return ' '.join(transcript)
        except HTTPError as e:
            if e.code == 429:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (2 ** attempt))  # Üstel geri çekilme
                    continue
            raise
        except Exception as e:
            logger.error(f"Transkript alınırken hata oluştu: {str(e)}")
            return None

    raise Exception("Maksimum yeniden deneme sayısına ulaşıldı")

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    
    video_id = extract_video_id(video_url)
    if not video_id:
        return jsonify({'error': 'Geçersiz YouTube URL\'si veya video ID\'si'}), 400
    
    try:
        transcript = rate_limited_get_youtube_transcript(video_id)
        
        if transcript:
            translated_text = translate_text(transcript, target_language)
            if translated_text is None:
                return jsonify({'error': 'Çeviri başarısız oldu'}), 500
            
            audio_filename = f"{video_id}_{int(time.time())}.mp3"
            audio_path = text_to_speech(translated_text, audio_filename, target_language)
            
            return jsonify({
                'video_id': video_id,
                'audio_file': f"/static/temp_audio/{audio_filename}",
                'target_language': target_language
            })
        else:
            return jsonify({'error': 'Transkript alınamadı veya video altyazı içermiyor'}), 500
    except Exception as e:
        logger.error(f"İşlem sırasında bir hata oluştu: {e}")
        return jsonify({'error': 'İşlem sırasında bir hata oluştu. Lütfen daha sonra tekrar deneyin.'}), 500

def translate_text(text, target_language):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Görev: Aşağıdaki metni {target_language} diline çevir.

    Kaynak Metin:
    {text[:4000]}  # İlk 4000 karakter ile sınırla

    Hedef Dil: {target_language}

    Çeviri Yönergeleri:
    1. Metni akıcı ve doğal bir {target_language} diline çevir.
    2. Orijinal metnin anlamını ve tonunu koru.
    3. Teknik terimleri ve özel isimleri uygun şekilde ele al.
    4. Kültürel referansları hedef dile uyarla.
    5. Tutarlı bir dil ve üslup kullan.

    Çevrilmiş Metin:
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
        return None

def get_language_code(language):
    language_codes = {
        'Türkçe': 'tr', 'İngilizce': 'en', 'Almanca': 'de', 'Fransızca': 'fr',
        'İspanyolca': 'es', 'İtalyanca': 'it', 'Rusça': 'ru', 'Japonca': 'ja',
        'Çince': 'zh-cn', 'Korece': 'ko'
    }
    return language_codes.get(language, 'en')

def text_to_speech(text, output_file, target_language):
    lang_code = get_language_code(target_language)
    tts = gTTS(text=text[:5000], lang=lang_code)  # İlk 5000 karakter ile sınırla
    file_path = os.path.join(TEMP_AUDIO_DIR, output_file)
    tts.save(file_path)
    logger.info(f"Ses dosyası kaydedildi: {file_path}")
    return file_path

@app.route('/')
def index():
    return render_template('index111.html')

@app.route('/static/temp_audio/<path:filename>')
def serve_audio(filename):
    file_path = os.path.join(TEMP_AUDIO_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return jsonify({'error': 'Ses dosyası bulunamadı'}), 404

@app.route('/share/<video_id>/<lang>')
def share_video(video_id, lang):
    return render_template('result.html', video_id=video_id, lang=lang)

@app.route('/result')
def result():
    video_id = request.args.get('video_id')
    lang = request.args.get('lang')
    audio_file = request.args.get('audio_file')
    return render_template('result.html', video_id=video_id, lang=lang, audio_file=audio_file)

if __name__ == "__main__":
    app.run(debug=True)
