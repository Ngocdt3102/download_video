from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import subprocess
import time
import shutil

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
            for f in formats_list:
                if not f.get('url'):
                    continue
                vcodec = f.get('vcodec', 'none')
                height = f.get('height')
                
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


# --- ROUTE MỚI: Phục vụ việc gửi file từ máy chủ về trình duyệt người dùng ---
@app.route('/api/download-file/<path:filename>', methods=['GET'])
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route('/api/download-progress', methods=['POST'])
def download_progress():
    # 1. Bắt dữ liệu cực kỳ an toàn từ Form (Giống hệt extract-info)
    url = request.form.get('url')
    format_id = request.form.get('format_id', 'best')

    # Dự phòng nếu gửi bằng JSON
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
        
        # Kiểm tra sự tồn tại của ffmpeg
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            yield f"data: [LOG] CẢNH BÁO: Không tìm thấy FFmpeg, chuyển sang tải trực tiếp...\n\n"
            ydl_format = 'best'
        else:
            yield f"data: [LOG] Đã phát hiện công cụ xử lý FFmpeg: {ffmpeg_path}\n\n"
            ydl_format = f"{format_id}+bestaudio/best" if format_id != 'best' else 'best'

        ydl_opts = {
            'format': ydl_format,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True
        }

        yield f"data: [LOG] Bắt đầu tải phần video và âm thanh...\n\n"
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                yield f"data: [LOG] Đang tiến hành tải xuống và đồng bộ hóa dữ liệu...\n\n"
                info = ydl.extract_info(url, download=True)
                
                # --- ĐIỂM QUAN TRỌNG: Lấy tên file thực tế sau khi tải xong ---
                filename = ydl.prepare_filename(info)
                base_filename = os.path.basename(filename)
                
            yield f"data: [LOG] Đang thực hiện ghép nối (muxing) video hoàn chỉnh...\n\n"
            time.sleep(1)
            yield f"data: [LOG] Đóng gói tệp tin thành công!\n\n"
            yield f"data: [PROGRESS] 100\n\n"
            
            # --- ĐIỂM QUAN TRỌNG: Gửi tên file về Frontend qua thẻ SUCCESS ---
            yield f"data: [SUCCESS] {base_filename}\n\n"

        except Exception as e:
            yield f"data: [LOG] LỖI TRONG QUÁ TRÌNH TẢI: {str(e)}\n\n"
            yield f"data: [ERROR] {str(e)}\n\n"

    return Response(generate_logs(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)