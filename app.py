from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import time
import shutil

# --- BÍ KÍP ÉP FFMPEG CHẠY TRÊN CLOUD ---
try:
    import imageio_ffmpeg
    # Lấy đường dẫn file chạy FFmpeg được cài ngầm bởi Python
    ffmpeg_exe_path = imageio_ffmpeg.get_ffmpeg_exe()
    # Ép nó vào biến môi trường hệ thống để shutil.which và yt-dlp nhìn thấy
    os.environ["PATH"] += os.pathsep + os.path.dirname(ffmpeg_exe_path)
except ImportError:
    pass # Dự phòng nếu bạn đang chạy test ở máy tính cá nhân mà chưa cài thư viện
# ----------------------------------------

app = Flask(__name__)
CORS(app)

# Thư mục lưu trữ tạm file video sau khi gộp
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
            has_audio = False # Biến kiểm tra xem video có chứa âm thanh không

            for f in formats_list:
                if not f.get('url'):
                    continue
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                height = f.get('height')

                # Cờ đánh dấu nếu tìm thấy luồng âm thanh
                if acodec != 'none':
                    has_audio = True
                
                # Lọc các định dạng video có độ phân giải
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

            # TỰ ĐỘNG THÊM OPTION AUDIO VÀO CUỐI DANH SÁCH NẾU CÓ HỖ TRỢ
            if has_audio:
                final_formats.append({
                    "format_id": "bestaudio", # ID đặc biệt để Backend dễ dàng bắt diện
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
        
        # Kiểm tra sự tồn tại của ffmpeg (Chắc chắn sẽ tìm thấy nhờ đoạn hack trên đầu file)
        ffmpeg_path = shutil.which('ffmpeg')
        is_audio_only = (format_id == 'bestaudio')

        if not ffmpeg_path:
            yield f"data: [LOG] CẢNH BÁO: Không tìm thấy FFmpeg, chuyển sang tải trực tiếp...\n\n"
            # Fallback liên hoàn chống lỗi TikTok
            if is_audio_only:
                ydl_format = 'bestaudio/b/best' 
            else:
                ydl_format = f"{format_id}/b/best" if format_id != 'best' else 'b/best'
        else:
            yield f"data: [LOG] Đã phát hiện công cụ xử lý FFmpeg: {ffmpeg_path}\n\n"
            if is_audio_only:
                ydl_format = 'bestaudio/b/best'
            else:
                # Ép gộp -> Tải file gộp sẵn -> Tải tốt nhất
                ydl_format = f"{format_id}+bestaudio/{format_id}/b/best" if format_id != 'best' else 'bestvideo+bestaudio/b/best'

        ydl_opts = {
            'format': ydl_format,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True
        }

        # Nếu là tải Audio, kích hoạt bộ PostProcessor để ép kiểu sang MP3
        if is_audio_only and ffmpeg_path:
            yield f"data: [LOG] Chế độ Audio kích hoạt. Chuẩn bị chuyển đổi MP3...\n\n"
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192', # Chuẩn chất lượng 192kbps mượt mà
            }]

        yield f"data: [LOG] Bắt đầu trích xuất luồng dữ liệu...\n\n"
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                yield f"data: [LOG] Đang tiến hành tải xuống và đồng bộ hóa...\n\n"
                info = ydl.extract_info(url, download=True)
                
                filename = ydl.prepare_filename(info)
                base_filename = os.path.basename(filename)
                
                # Nếu qua bước convert MP3, yt-dlp sẽ tự động đổi đuôi file thực tế thành .mp3
                # Do đó ta cần báo lại cho Frontend biết tên file mp3 chính xác để tải về
                if is_audio_only and ffmpeg_path:
                    base_filename = os.path.splitext(base_filename)[0] + '.mp3'
                
            yield f"data: [LOG] Đang xử lý tín hiệu và đóng gói tệp tin...\n\n"
            time.sleep(1)
            yield f"data: [LOG] Đóng gói thành công!\n\n"
            yield f"data: [PROGRESS] 100\n\n"
            
            yield f"data: [SUCCESS] {base_filename}\n\n"

        except Exception as e:
            yield f"data: [LOG] LỖI TRONG QUÁ TRÌNH TẢI: {str(e)}\n\n"
            yield f"data: [ERROR] {str(e)}\n\n"

    return Response(generate_logs(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)