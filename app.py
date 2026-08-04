from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp
import os
import subprocess
import shutil
import queue
import threading
import re

ffmpeg_exe = shutil.which('ffmpeg')
try:
    import imageio_ffmpeg
    if not ffmpeg_exe:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    pass

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

# --- BẬT KHIÊN CHỐNG SPAM (Flask-Limiter) ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "10 per minute"],
    storage_uri="memory://"
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "online", "message": "Nexus Downloader Engine is running!"}), 200

@app.route('/api/extract-info', methods=['POST'])
@limiter.limit("10 per minute")
def extract_info():
    url = request.form.get('url')
    if not url and request.is_json:
        req_data = request.get_json(silent=True)
        if req_data:
            url = req_data.get('url')

    if not url:
        return jsonify({"error": "Vui lòng cung cấp URL video"}), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats_list = info.get('formats', [])
            
            video_dict = {}
            has_audio = False

            for f in formats_list:
                if not f.get('url'):
                    continue
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                height = f.get('height')

                if acodec != 'none':
                    has_audio = True
                
                if vcodec != 'none' and height:
                    if height not in video_dict:
                        video_dict[height] = {
                            "format_id": f.get('format_id'),
                            "resolution": f"{height}p",
                            "ext": f.get('ext', 'mp4'),
                            "height": height
                        }

            sorted_videos = sorted(video_dict.values(), key=lambda x: x['height'], reverse=True)
            final_formats = []

            for idx, v in enumerate(sorted_videos):
                label = v['resolution']
                if idx == 0:
                    label += " ⭐ (Recommend)"
                final_formats.append({
                    "format_id": v['format_id'],
                    "resolution": label,
                    "ext": 'mp4',
                })

            if has_audio:
                final_formats.append({
                    "format_id": "bestaudio",
                    "resolution": "Audio 🎧 (MP3 - Tốt nhất)",
                    "ext": "mp3"
                })

            response_data = {
                "title": info.get('title'),
                "thumbnail": info.get('thumbnail'),
                "duration": info.get('duration'),
                "platform": info.get('extractor'),
                "formats": final_formats,
                "original_url": url
            }

            return jsonify(response_data), 200

    except Exception as e:
        return jsonify({"error": f"Không thể phân tích video: {str(e)}"}), 500


@app.route('/api/download-file/<path:filename>', methods=['GET'])
def download_file(filename):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": "File không tồn tại hoặc đã bị xóa"}), 404
        
    try:
        response = send_file(
            file_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/octet-stream'
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
    except Exception as e:
        return str(e), 500


# =====================================================================
# HÀM 1: CHUYÊN XỬ LÝ TẢI VIDEO (MP4)
# =====================================================================
@app.route('/api/download/video', methods=['GET', 'POST'])
@limiter.limit("3 per minute") # Tối đa 3 video/phút mỗi IP
def download_video():
    url = request.args.get('url') if request.method == 'GET' else request.form.get('url')
    format_id = request.args.get('format_id', 'best') if request.method == 'GET' else request.form.get('format_id', 'best')

    if not url:
        return jsonify({"error": "Thiếu URL video"}), 400

    q = queue.Queue()

    def progress_hook(d):
        if d['status'] == 'downloading':
            p_clean = re.sub(r'\x1b[^m]*m', '', d.get('_percent_str', '0%')).strip().replace('%', '')
            try: q.put(f"data: [PROGRESS] {float(p_clean)}\n\n")
            except ValueError: pass
        elif d['status'] == 'finished':
            q.put(f"data: [LOG] Tải xuống hoàn tất, đang đóng gói Video...\n\n")

    def run_download_thread():
        ydl_format = f"{format_id}+bestaudio/best" if format_id != 'best' else 'bestvideo+bestaudio/best'
        ydl_opts = {
            'format': ydl_format,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
            'postprocessor_args': ['-threads', '1'],
            'progress_hooks': [progress_hook],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'en-us,en;q=0.5',
            }
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                q.put(f"DONE:{os.path.basename(filename)}")
        except Exception as e:
            q.put(f"ERROR:{str(e)}")

    def generate_logs():
        yield ":" + (" " * 2048) + "\n\n"
        yield f"data: [LOG] Khởi tạo luồng xử lý VIDEO...\n\n"
        threading.Thread(target=run_download_thread).start()
        
        while True:
            try:
                msg = q.get(timeout=10)
                if msg.startswith("DONE:"):
                    yield f"data: [PROGRESS] 100\n\ndata: [SUCCESS] {msg.split(':', 1)[1]}\n\n"
                    break
                elif msg.startswith("ERROR:"):
                    yield f"data: [ERROR] {msg.split(':', 1)[1]}\n\n"
                    break
                else: yield msg
            except queue.Empty:
                yield f"data: [LOG] Đang xử lý Video... (Vui lòng không đóng trang)\n\n"

    # Bùa chú chống Cache và ép tường lửa xả bộ đệm liên tục
    headers = {
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    }
    return Response(generate_logs(), mimetype='text/event-stream', headers=headers)


# =====================================================================
# HÀM 2: CHUYÊN XỬ LÝ TẢI AUDIO (MP3)
# =====================================================================
@app.route('/api/download/audio', methods=['GET', 'POST'])
@limiter.limit("3 per minute") # Tối đa 3 audio/phút mỗi IP
def download_audio():
    url = request.args.get('url') if request.method == 'GET' else request.form.get('url')

    if not url:
        return jsonify({"error": "Thiếu URL âm thanh"}), 400

    q = queue.Queue()

    def progress_hook(d):
        if d['status'] == 'downloading':
            p_clean = re.sub(r'\x1b[^m]*m', '', d.get('_percent_str', '0%')).strip().replace('%', '')
            try: q.put(f"data: [PROGRESS] {float(p_clean)}\n\n")
            except ValueError: pass
        elif d['status'] == 'finished':
            q.put(f"data: [LOG] Tải xuống hoàn tất, chuẩn bị bóc tách MP3...\n\n")

    def run_download_thread():
        ydl_opts = {
            'format': 'bestaudio/best', # Chỉ lấy audio ngay từ đầu
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
            'progress_hooks': [progress_hook],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
            }
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                base_filename = os.path.basename(filename)
                
                # Ép nén MP3 an toàn cho RAM
                if ffmpeg_exe:
                    q.put("data: [LOG] Đang chuyển đổi sang định dạng MP3 (Chạy ngầm 1 luồng)...\n\n")
                    mp3_filename = os.path.splitext(filename)[0] + '.mp3'
                    try:
                        subprocess.run([
                            ffmpeg_exe, '-y', '-i', filename,
                            '-vn', '-acodec', 'libmp3lame', '-b:a', '192k',
                            '-threads', '1', mp3_filename
                        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        if os.path.exists(filename) and filename != mp3_filename:
                            os.remove(filename)
                        base_filename = os.path.basename(mp3_filename)
                    except subprocess.CalledProcessError:
                        q.put("data: [LOG] LỖI: Bóc tách thất bại, trả về file gốc...\n\n")

                q.put(f"DONE:{base_filename}")
        except Exception as e:
            q.put(f"ERROR:{str(e)}")

    def generate_logs():
        yield ":" + (" " * 2048) + "\n\n"
        yield f"data: [LOG] Khởi tạo luồng xử lý AUDIO...\n\n"
        threading.Thread(target=run_download_thread).start()
        
        while True:
            try:
                msg = q.get(timeout=10)
                if msg.startswith("DONE:"):
                    yield f"data: [PROGRESS] 100\n\ndata: [SUCCESS] {msg.split(':', 1)[1]}\n\n"
                    break
                elif msg.startswith("ERROR:"):
                    yield f"data: [ERROR] {msg.split(':', 1)[1]}\n\n"
                    break
                else: yield msg
            except queue.Empty:
                yield f"data: [LOG] Đang xử lý Audio... (Vui lòng không đóng trang)\n\n"

    # Bùa chú chống Cache và ép tường lửa xả bộ đệm liên tục
    headers = {
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    }
    return Response(generate_logs(), mimetype='text/event-stream', headers=headers)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)