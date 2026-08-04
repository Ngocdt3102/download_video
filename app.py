from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import yt_dlp
import os
import subprocess
import shutil
import queue
import threading
import re

# --- BÍ KÍP ÉP FFMPEG CHẠY TRÊN CLOUD CHỈ VỚI 1 DÒNG ---
ffmpeg_exe = shutil.which('ffmpeg')
try:
    import imageio_ffmpeg
    if not ffmpeg_exe:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    pass
# -------------------------------------------------------

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "online", "message": "Nexus Downloader Engine is running!"}), 200

@app.route('/api/extract-info', methods=['POST'])
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


@app.route('/api/download-progress', methods=['POST'])
def download_progress():
    url = request.form.get('url')
    format_id = request.form.get('format_id', 'best')

    if not url and request.is_json:
        req_data = request.get_json(silent=True) or {}
        url = req_data.get('url')
        format_id = req_data.get('format_id', 'best')

    if not url:
        return jsonify({"error": "Thiếu URL video"}), 400

    # 1. Hàng đợi để giao tiếp giữa Luồng tải nền và Luồng gửi phản hồi SSE
    q = queue.Queue()

    # Hàm móc vào yt-dlp để lấy % tiến trình thật
    def progress_hook(d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%')
            # Làm sạch mã màu ANSI của console
            p_clean = re.sub(r'\x1b[^m]*m', '', p).strip().replace('%', '')
            try:
                q.put(f"data: [PROGRESS] {float(p_clean)}\n\n")
            except ValueError:
                pass
        elif d['status'] == 'finished':
            q.put(f"data: [LOG] Tải xuống hoàn tất, đang đóng gói dữ liệu...\n\n")

    # Hàm chạy nền để không làm nghẽn Event Stream
    def run_download_thread():
        is_audio_only = (format_id == 'bestaudio')
        
        if not ffmpeg_exe:
            q.put(f"data: [LOG] CẢNH BÁO: Không có FFmpeg, chuyển sang tải trực tiếp...\n\n")
            ydl_format = 'bestaudio/b/best' if is_audio_only else (f"{format_id}/b/best" if format_id != 'best' else 'b/best')
        else:
            q.put(f"data: [LOG] Đã kích hoạt lõi FFmpeg chống nghẽn...\n\n")
            ydl_format = 'b/best' if is_audio_only else (f"{format_id}+bestaudio/{format_id}/b/best" if format_id != 'best' else 'bestvideo+bestaudio/b/best')

        ydl_opts = {
            'format': ydl_format,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
            'postprocessor_args': ['-threads', '1'],
            'progress_hooks': [progress_hook] # Móc vào tiến trình
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                base_filename = os.path.basename(filename)
                
                # Nén MP3 thủ công (bảo vệ RAM máy chủ)
                if is_audio_only and ffmpeg_exe:
                    q.put("data: [LOG] Đang bóc tách Audio (Quá trình này có thể mất chút thời gian)...\n\n")
                    mp3_filename = os.path.splitext(filename)[0] + '.mp3'
                    base_filename = os.path.basename(mp3_filename)
                    
                    try:
                        subprocess.run([
                            ffmpeg_exe, '-y', '-i', filename,
                            '-vn', '-acodec', 'libmp3lame', '-b:a', '128k',
                            '-threads', '1', mp3_filename
                        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        if os.path.exists(filename) and filename != mp3_filename:
                            os.remove(filename)
                            
                    except subprocess.CalledProcessError:
                        q.put("data: [LOG] LỖI: Bóc tách thất bại, đang trả về file gốc...\n\n")
                        base_filename = os.path.basename(filename)

            q.put(f"DONE:{base_filename}")
        except Exception as e:
            q.put(f"ERROR:{str(e)}")

    def generate_logs():
        yield f"data: [LOG] Bắt đầu kết nối tới hệ thống...\n\n"
        yield f"data: [LOG] Đang phân tích liên kết...\n\n"
        
        # Bật luồng tải ngầm để luồng này rảnh tay bơm dữ liệu
        threading.Thread(target=run_download_thread).start()

        while True:
            try:
                # Ép Timeout 10s: Nếu yt-dlp đang im lặng nén file, Queue sẽ trống sau 10s
                msg = q.get(timeout=10)
                
                if msg.startswith("DONE:"):
                    filename = msg.split(":", 1)[1]
                    yield f"data: [LOG] Đóng gói thành công!\n\n"
                    yield f"data: [PROGRESS] 100\n\n"
                    yield f"data: [SUCCESS] {filename}\n\n"
                    break
                elif msg.startswith("ERROR:"):
                    error_msg = msg.split(":", 1)[1]
                    yield f"data: [LOG] LỖI TRONG QUÁ TRÌNH TẢI: {error_msg}\n\n"
                    yield f"data: [ERROR] {error_msg}\n\n"
                    break
                else:
                    yield msg # Bơm % tiến trình thật hoặc log ra giao diện
            except queue.Empty:
                # [QUAN TRỌNG NHẤT]: Tim đập (Heartbeat) để giữ proxy Cloud không cắt dây mạng
                yield f"data: [LOG] Đang xử lý... (Vui lòng không đóng trang)\n\n"

    # Thêm Header vô hiệu hóa bộ đệm X-Accel để xuyên tường lửa Nginx/Cloudflare
    headers = {
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    }
    return Response(generate_logs(), mimetype='text/event-stream', headers=headers)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)