import os
import re
import time
import random
import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from cachetools import TTLCache, cached
from functools import wraps
from gtts import gTTS
import google.generativeai as genai

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Logging ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

genai.configure(api_key="AIzaSyBWC2gp-UjhlnGQgM0S77lftXnSl0uhqQ0")

# Geçici ses dosyaları için dizin
TEMP_AUDIO_DIR = os.path.join(app.root_path, 'static', 'temp_audio')
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

# Önbellek ayarları
transcript_cache = TTLCache(maxsize=1000, ttl=3600)  # 1 saat süreyle 1000 öğe sakla

class TranscriptsDisabled(Exception):
    pass

class NoTranscriptAvailable(Exception):
    pass

class YouTubeTranscriptApi:
    @staticmethod
    def get_transcript(video_id):
        try:
            # Video sayfasını al
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            html = urllib.request.urlopen(video_url).read().decode('utf-8')
            
            # Transkript URL'sini bul
            caption_url_regex = r'"captionTracks":\[.*?"baseUrl":"(.*?)"'
            caption_url_match = re.search(caption_url_regex, html, re.DOTALL)
            
            if not caption_url_match:
                raise TranscriptsDisabled("Bu video için transkriptler devre dışı bırakılmış.")
            
            caption_url = caption_url_match.group(1)
            caption_url = caption_url.replace('\\u0026', '&')
            
            # Transkript XML'ini indir ve parse et
            xml = urllib.request.urlopen(caption_url).read().decode('utf-8')
            root = ET.fromstring(xml)
            
            # Transkript metnini ve zaman damgalarını birleştir
            transcript = []
            for element in root.findall('.//text'):
                start = float(element.get('start'))
                duration = float(element.get('dur')) if element.get('dur') else 0
                text = element.text
                transcript.append({
                    'text': text,
                    'start': start,
                    'duration': duration
                })
            
            if not transcript:
                raise NoTranscriptAvailable("Bu video için transkript mevcut değil.")
            
            return transcript
        except TranscriptsDisabled:
            raise
        except NoTranscriptAvailable:
            raise
        except Exception as e:
            raise Exception(f"Transkript alınırken bir hata oluştu: {str(e)}")

def extract_video_id(url_or_id):
    # YouTube URL'sinden video ID'sini çıkar
    match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url_or_id)
    if match:
        return match.group(1)
    # Eğer zaten bir video ID ise, olduğu gibi döndür
    elif len(url_or_id) == 11:
        return url_or_id
    else:
        raise ValueError("Geçersiz YouTube URL'si veya video ID'si")

def exponential_backoff(attempt, base_delay=5, max_delay=300):
    delay = min(max_delay, base_delay * (2 ** attempt) + random.uniform(0, 1))
    return delay

def retry_with_backoff(retries=5):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"Final attempt failed: {e}")
                        raise e
                    wait_time = exponential_backoff(attempt)
                    logger.warning(f"Attempt {attempt + 1} failed. Waiting for {wait_time:.2f} seconds.")
                    time.sleep(wait_time)
        return wrapper
    return decorator

@retry_with_backoff(retries=5)
@cached(cache=transcript_cache)
def get_youtube_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return ' '.join([entry['text'] for entry in transcript])
    except TranscriptsDisabled:
        logger.warning(f"Transcripts are disabled for video {video_id}")
    except NoTranscriptAvailable:
        logger.warning(f"No transcript available for video {video_id}")
    except Exception as e:
        logger.error(f"Unexpected error while fetching transcript: {e}")
    return None

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
        logger.error(f"Translation error: {e}")
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
    logger.info(f"Audio file saved as {file_path}")
    return file_path

@app.route('/')
def index():
    return render_template('index111.html')

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    
    try:
        video_id = extract_video_id(video_url)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    transcript = get_youtube_transcript(video_id)
    
    if transcript:
        try:
            translated_text = translate_text(transcript, target_language)
            if translated_text is None:
                return jsonify({'error': 'Translation failed'}), 500
            
            audio_filename = f"{video_id}_{int(time.time())}.mp3"
            audio_path = text_to_speech(translated_text, audio_filename, target_language)
            
            return jsonify({
                'video_id': video_id,
                'audio_file': f"/static/temp_audio/{audio_filename}",
                'target_language': target_language
            })
        except Exception as e:
            logger.error(f"Error during translation or speech conversion: {e}")
            return jsonify({'error': 'An error occurred during processing'}), 500
    else:
        return jsonify({'error': 'Unable to fetch transcript or video has no captions'}), 500

@app.route('/static/temp_audio/<path:filename>')
def serve_audio(filename):
    file_path = os.path.join(TEMP_AUDIO_DIR, filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return jsonify({'error': 'Audio file not found'}), 404

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
