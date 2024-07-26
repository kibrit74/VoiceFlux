import os
import re
import time
import random
from flask import Flask, render_template, request, jsonify, send_file, url_for
from werkzeug.utils import secure_filename
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import google.generativeai as genai
from gtts import gTTS
from functools import wraps, lru_cache

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Google Gemini API anahtarınızı buraya ekleyin
genai.configure(api_key="AIzaSyBWC2gp-UjhlnGQgM0S77lftXnSl0uhqQ0")

# Geçici ses dosyaları için dizin
TEMP_AUDIO_DIR = os.path.join(app.root_path, 'static', 'temp_audio')
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)

def exponential_backoff(attempt):
    return min(300, (2 ** attempt) + random.random())

def retry_with_backoff(retries=3):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        raise e
                    wait_time = exponential_backoff(attempt)
                    print(f"Attempt {attempt + 1} failed. Waiting for {wait_time:.2f} seconds.")
                    time.sleep(wait_time)
        return wrapper
    return decorator

def extract_video_id(url):
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)\/(?:watch\?v=)?(.{11})'
    match = re.search(pattern, url)
    return match.group(1) if match else None

@retry_with_backoff(retries=3)
@lru_cache(maxsize=100)
def get_youtube_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return ' '.join([entry['text'] for entry in transcript])
    except TranscriptsDisabled:
        print(f"Video {video_id} için transkript devre dışı bırakılmış.")
    except NoTranscriptFound:
        print(f"Video {video_id} için transkript bulunamadı.")
    except Exception as e:
        print(f"Transkript alınırken beklenmeyen bir hata oluştu: {e}")
    return None

def translate_text(text, target_language):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Görev: Aşağıdaki metni {target_language} diline çevir.

    Kaynak Metin:
    {text}

    Hedef Dil: {target_language}

    Çeviri Yönergeleri:
    1. Metni akıcı ve doğal bir {target_language} diline çevir.
    2. Orijinal metnin anlamını ve tonunu koruyarak çeviri yap.
    3. Teknik terimleri veya özel isimleri uygun şekilde ele al; gerektiğinde parantez içinde açıklama ekle.
    4. Kültürel referansları hedef dilin kültürüne uygun şekilde uyarla, ancak orijinal anlamı kaybetme.
    5. Deyimler ve atasözlerini hedef dildeki eşdeğerleriyle değiştir.
    6. Cümle yapılarını hedef dilin gramer kurallarına uygun olacak şekilde yeniden düzenle.
    7. Çeviride tutarlı bir dil ve üslup kullan.
    8. Hedef dilin resmi veya günlük kullanımına uygun bir dil seviyesi seç.
    9. Gerektiğinde, kaynak dildeki belirsiz ifadeleri netleştir.
    10. Çevirinin doğruluğundan emin olmadığın kısımları [?] işareti ile belirt.

    Çevrilmiş Metin:
    """
    response = model.generate_content(prompt)
    return response.text

def get_language_code(language):
    language_codes = {
        'Türkçe': 'tr', 'İngilizce': 'en', 'Almanca': 'de', 'Fransızca': 'fr',
        'İspanyolca': 'es', 'İtalyanca': 'it', 'Rusça': 'ru', 'Japonca': 'ja',
        'Çince': 'zh-cn', 'Korece': 'ko'
    }
    return language_codes.get(language, 'en')

def text_to_speech(text, output_file, target_language):
    lang_code = get_language_code(target_language)
    tts = gTTS(text=text, lang=lang_code)
    file_path = os.path.join(TEMP_AUDIO_DIR, output_file)
    tts.save(file_path)
    print(f"Ses dosyası {file_path} olarak kaydedildi.")
    
    if os.path.exists(file_path):
        print(f"Dosya başarıyla oluşturuldu: {file_path}")
        print(f"Dosya boyutu: {os.path.getsize(file_path)} bytes")
    else:
        print(f"Hata: Dosya oluşturulamadı: {file_path}")
    
    return file_path

@app.route('/')
def index():
    return render_template('index111.html')

@app.route('/translate', methods=['POST'])
def translate():
    video_url = request.form['video_url']
    target_language = request.form['target_language']
    video_id = extract_video_id(video_url)
    
    if not video_id:
        return jsonify({'error': 'Geçersiz YouTube URL\'si'}), 400
    
    transcript = get_youtube_transcript(video_id)
    
    if transcript:
        try:
            translated_text = translate_text(transcript, target_language)
            audio_filename = f"{video_id}_{int(time.time())}.mp3"
            audio_path = text_to_speech(translated_text, audio_filename, target_language)
            
            full_audio_path = os.path.join(app.root_path, 'static', 'temp_audio', audio_filename)
            if os.path.exists(full_audio_path):
                print(f"Ses dosyası mevcut: {full_audio_path}")
            else:
                print(f"Hata: Ses dosyası bulunamadı: {full_audio_path}")
            
            return jsonify({
                'video_id': video_id,
                'audio_file': f"/static/temp_audio/{audio_filename}",
                'target_language': target_language
            })
        except Exception as e:
            print(f"Çeviri veya ses dönüşümü hatası: {e}")
            return jsonify({'error': 'Çeviri işlemi sırasında bir hata oluştu'}), 500
    else:
        return jsonify({'error': 'Transkript alınamadı veya video altyazı içermiyor'}), 500

@app.route('/static/temp_audio/<path:filename>')
def serve_audio(filename):
    file_path = os.path.join(TEMP_AUDIO_DIR, filename)
    if os.path.exists(file_path):
        print(f"Serving audio file: {file_path}")
        return send_file(file_path, as_attachment=True)
    else:
        print(f"Error: Audio file not found: {file_path}")
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
