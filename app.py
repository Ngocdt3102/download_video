from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import time
import subprocess
import shutil

# --- BÍ KÍP ÉP FFMPEG CHẠY TRÊN CLOUD CHỈ VỚI 1 DÒNG ---
ffmpeg_exe = shutil.which('ffmpeg')
try:
    import imageio_ffmpeg
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

            # TỰ ĐỘNG THÊM OPTION AUDIO DÀNH CHO BẠN GÁI 🎧
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
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


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

    def generate_logs():
        yield f"data: [LOG] Bắt đầu kết nối tới hệ thống xử lý...\n\n"
        time.sleep(0.5)
        yield f"data: [LOG] Đang phân tích liên kết: {url}\n\n"
        
        is_audio_only = (format_id == 'bestaudio')

        if not ffmpeg_exe:
            yield f"data: [LOG] CẢNH BÁO: Không tìm thấy FFmpeg, chuyển sang tải trực tiếp...\n\n"
            ydl_format = 'bestaudio/b/best' if is_audio_only else (f"{format_id}/b/best" if format_id != 'best' else 'b/best')
        else:
            yield f"data: [LOG] Đã kích hoạt lõi FFmpeg chống sập RAM...\n\n"
            # TikTok không có Audio riêng, nên ta tải file Video tốt nhất về để tự cắt tiếng
            ydl_format = 'b/best' if is_audio_only else (f"{format_id}+bestaudio/{format_id}/b/best" if format_id != 'best' else 'bestvideo+bestaudio/b/best')

        ydl_opts = {
            'format': ydl_format,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            # Bắt buộc yt-dlp gộp video (nếu có) bằng 1 luồng để tiết kiệm RAM
            'postprocessor_args': ['-threads', '1'] 
        }

        yield f"data: [LOG] Bắt đầu trích xuất luồng dữ liệu...\n\n"
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                yield f"data: [LOG] Đang tiến hành tải xuống và đồng bộ hóa...\n\n"
                info = ydl.extract_info(url, download=True)
                
                filename = ydl.prepare_filename(info)
                base_filename = os.path.basename(filename)
                
                # --- CHUYỂN ĐỔI MP3 THỦ CÔNG & TIẾT KIỆM RAM (CHỐNG LỖI 502) ---
                if is_audio_only and ffmpeg_exe:
                    yield f"data: [LOG] Đang bóc tách Audio (Chế độ tối ưu bộ nhớ)...\n\n"
                    mp3_filename = os.path.splitext(filename)[0] + '.mp3'
                    base_filename = os.path.basename(mp3_filename)
                    
                    try:
                        # Gọi trực tiếp FFmpeg: Lọc bỏ hình ảnh (-vn) và giới hạn đúng 1 luồng CPU (-threads 1)
                        subprocess.run([
                            ffmpeg_exe, '-y', '-i', filename,
                            '-vn',
                            '-acodec', 'libmp3lame', '-b:a', '128k',
                            '-threads', '1',
                            mp3_filename
                        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        # Xóa file video gốc để giải phóng dung lượng cho máy chủ
                        if os.path.exists(filename) and filename != mp3_filename:
                            os.remove(filename)
                            
                    except subprocess.CalledProcessError:
                        yield f"data: [LOG] LỖI: Bóc tách thất bại, đang trả về file gốc...\n\n"
                        base_filename = os.path.basename(filename)
                
            yield f"data: [LOG] Đóng gói thành công!\n\n"
            yield f"data: [PROGRESS] 100\n\n"
            yield f"data: [SUCCESS] {base_filename}\n\n"

        except Exception as e:
            yield f"data: [LOG] LỖI TRONG QUÁ TRÌNH TẢI: {str(e)}\n\n"
            yield f"data: [ERROR] {str(e)}\n\n"

    return Response(generate_logs(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)