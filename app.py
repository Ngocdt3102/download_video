from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import os

app = Flask(__name__)
CORS(app)

@app.route('/api/extract-info', methods=['POST'])
def extract_info():
    url = request.form.get('url')
    if not url and request.is_json:
        req_data = request.get_json(silent=True)
        if req_data:
            url = req_data.get('url')

    if not url:
        return jsonify({"error": "Vui lòng cung cấp URL video"}), 400

    # Cấu hình yt-dlp ưu tiên gộp hình và tiếng (yêu cầu môi trường có ffmpeg)
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            response_data = {
                "title": info.get('title'),
                "thumbnail": info.get('thumbnail'),
                "duration": info.get('duration'),
                "platform": info.get('extractor'),
                "formats": []
            }

            formats_list = info.get('formats', [])
            video_dict = {}
            audio_formats = []

            for f in formats_list:
                if not f.get('url'):
                    continue
                
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                ext = f.get('ext', 'mp4')
                height = f.get('height')

                # Lọc audio riêng lẻ chất lượng cao
                if vcodec == 'none' and acodec != 'none':
                    audio_formats.append({
                        "url": f.get('url'),
                        "ext": ext if ext in ['mp3', 'm4a', 'webm'] else 'm4a',
                        "quality": f.get('tbr', 0) or 0
                    })
                    continue

                # Lọc các luồng video có độ phân giải chuẩn
                if vcodec != 'none' and height and ext == 'mp4':
                    if height not in video_dict:
                        video_dict[height] = {
                            "format_id": f.get('format_id'),
                            "resolution": f"{height}p",
                            "ext": 'mp4',
                            "url": f.get('url'), # Link video thô (hoặc link kết hợp nếu có)
                            "filesize": f.get('filesize'),
                            "height": height,
                            "has_audio": acodec != 'none' # Kiểm tra xem luồng này đã có sẵn tiếng chưa
                        }

            sorted_videos = sorted(video_dict.values(), key=lambda x: x['height'], reverse=True)
            final_formats = []

            for idx, v in enumerate(sorted_videos):
                label = v['resolution']
                if not v['has_audio']:
                    label += " (Full HD/HD)"  # Đánh dấu các bản cần gộp hoặc bản tiêu chuẩn
                
                if idx == 0:
                    label += " ⭐ (Recommend)"

                final_formats.append({
                    "format_id": v['format_id'],
                    "resolution": label,
                    "ext": v['ext'],
                    "url": v['url'],
                    "filesize": v['filesize']
                })

            # Thêm option Audio Only ở cuối
            if audio_formats:
                best_audio = max(audio_formats, key=lambda x: x['quality'])
                final_formats.append({
                    "format_id": "audio_hq",
                    "resolution": "Audio Only (HQ)",
                    "ext": best_audio['ext'],
                    "url": best_audio['url'],
                    "filesize": None
                })

            response_data["formats"] = final_formats

            if not response_data["formats"]:
                return jsonify({"error": "Không tìm thấy định dạng tải xuống phù hợp."}), 400

            return jsonify(response_data), 200

    except Exception as e:
        return jsonify({"error": f"Không thể xử lý video: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)