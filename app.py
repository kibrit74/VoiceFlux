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

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

genai.configure(api_key="AIzaSyBWC2gp-UjhlnGQgM0S77lftXnSl0uhqQ0")

TEMP_AUDIO_DIR = os.path.join(app.root_path, 'static', 'temp_audio')
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

transcript_cache = TTLCache(maxsize=1000, ttl=3600)

class TranscriptsDisabled(Exception):
    pass

class NoTranscriptAvailable(Exception):
    pass

class TooManyRequests(Exception):
    pass

class YouTubeTranscriptApi:
    @staticmethod
    def get_transcript(video_id):
        try:
            video_url = f'https://www.youtube.com/watch?v={video_id}'
            req = urllib.request.Request(video_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                html = response.read().decode('utf-8')
            
            caption_url_regex = r'"captionTracks":\[.*?"baseUrl":"(.*?)"'
            caption_url_match = re.search(caption_url_regex, html, re.DOTALL)
            
            if not caption_url_match:
                raise TranscriptsDisabled("Transkriptler devre dışı bırakılmış.")
            
            caption_url = caption_url_match.group(1)
            caption_url = caption_url.replace('\\u0026', '&')
            
            req = urllib.request.Request(caption_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                xml = response.read().decode('utf-8')
            root = ET.fromstring(xml)
            
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
                raise NoTranscriptAvailable("Transkript mevcut değil.")
            
            return transcript
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise TooManyRequests("Çok fazla istek gönderildi. Lütfen daha sonra tekrar deneyin.")
            logger.error(f"HTTP Hatası: {e.code} - {e.reason}")
            raise
        except Exception as e:
            logger.error(f"Transkript alınırken bir hata oluştu: {str(e)}")
            raise

def exponential_backoff(attempt, base_delay=30, max_delay=3600):
    delay = min(max_delay, (base_delay * (2 ** attempt)) + (random.random() * 30))
    return delay

def retry_with_backoff(retries=15, base_delay=30, max_delay=3600):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except TooManyRequests:
                    if attempt == retries - 1:
                        logger.error("Maksimum yeniden deneme sayısına ulaşıldı.")
                        raise
                    wait_time = exponential_backoff(attempt, base_delay, max_delay)
                    logger.warning(f"Çok fazla istek. {wait_time:.2f} saniye bekleniyor...")
                    time.sleep(wait_time)
                except Exception as e:
                    logger.error(f"Hata: {e}")
                    if attempt == retries - 1:
                        raise
            return None
        return wrapper
    return decorator

def extract_video_id(url_or_id):
    match = re.search(r'(?:v=|\/)([0-9A-Za-z_-]{11}).*', url_or_id)
    if match:
        return match.group(1)
    elif len(url_or_id) == 11:
        return url_or_id
    else:
        raise ValueError("Geçersiz YouTube URL'si veya video ID'si")

@retry_with_backoff(retries=15, base_delay=30, max_delay=3600)
@cached(cache=transcript_cache)
def get_youtube_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return ' '.join([entry['text'] for entry in transcript])
    except TranscriptsDisabled:
        logger.warning(f"Video {video_id} için transkriptler devre dışı.")
    except NoTranscriptAvailable:
        logger.warning(f"Video {video_id} için transkript mevcut değil.")
    except TooManyRequests:
        logger.error("Çok fazla istek gönderildi. Yeniden deneniyor...")
        raise
    except Exception as e:
        logger.error(f"Transkript alınırken beklenmeyen bir hata oluştu: {e}")
    return None

# ... (diğer fonksiyonlar aynı kalıyor)

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    
    try:
        video_id = extract_video_id(video_url)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    
    try:
        transcript = get_youtube_transcript(video_id)
        
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
    except TooManyRequests:
        return jsonify({'error': 'Şu anda çok fazla istek var. Lütfen daha sonra tekrar deneyin.'}), 429
    except Exception as e:
        logger.error(f"İşlem sırasında bir hata oluştu: {e}")
        return jsonify({'error': 'İşlem sırasında bir hata oluştu'}), 500

if __name__ == "__main__":
    app.run(debug=True)
