from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp

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

    ydl_opts = {
        'format': 'best',
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
            audio_list = []

            for f in formats_list:
                if not f.get('url'):
                    continue
                
                vcodec = f.get('vcodec', 'none')
                acodec = f.get('acodec', 'none')
                ext = f.get('ext', 'mp4')
                height = f.get('height')

                # 1. Thu thập danh sách Audio riêng biệt
                if vcodec == 'none' and acodec != 'none':
                    audio_list.append({
                        "format_id": f.get('format_id'),
                        "resolution": "Audio Only (HQ)",
                        "ext": ext if ext in ['mp3', 'm4a', 'webm'] else 'm4a',
                        "url": f.get('url'),
                        "filesize": f.get('filesize'),
                        "quality": f.get('tbr', 0) or 0
                    })
                    continue

                # 2. Thu thập danh sách Video
                if vcodec != 'none' and height:
                    res_key = height
                    # Tính điểm ưu tiên: ưu tiên đuôi mp4 và định dạng có sẵn cả âm thanh
                    score = 0
                    if ext == 'mp4':
                        score += 2
                    if acodec != 'none':
                        score += 5
                    
                    if res_key not in video_dict or score > video_dict[res_key]['score']:
                        video_dict[res_key] = {
                            "format_id": f.get('format_id'),
                            "resolution": f"{height}p",
                            "ext": ext,
                            "url": f.get('url'),
                            "filesize": f.get('filesize'),
                            "height": height,
                            "score": score
                        }

            # Sắp xếp các độ phân giải video từ cao xuống thấp (VD: 1080p -> 720p -> 480p...)
            sorted_videos = sorted(video_dict.values(), key=lambda x: x['height'], reverse=True)

            final_formats = []

            # Đưa toàn bộ option video lên đầu và gắn nhãn Recommend cho option sắc nét nhất (phần tử đầu tiên)
            for idx, v in enumerate(sorted_videos):
                label = v['resolution']
                if idx == 0:
                    label += " ⭐ (Recommend)" # Gắn nhãn đề xuất cho chất lượng cao nhất

                final_formats.append({
                    "format_id": v['format_id'],
                    "resolution": label,
                    "ext": v['ext'],
                    "url": v['url'],
                    "filesize": v['filesize']
                })

            # Đưa option Audio xuống dưới cùng (vì ưu tiên video)
            if audio_list:
                best_audio = max(audio_list, key=lambda x: x['quality'])
                final_formats.append({
                    "format_id": best_audio['format_id'],
                    "resolution": best_audio['resolution'],
                    "ext": best_audio['ext'],
                    "url": best_audio['url'],
                    "filesize": best_audio['filesize']
                })

            response_data["formats"] = final_formats

            if not response_data["formats"]:
                return jsonify({"error": "Không tìm thấy định dạng tải xuống phù hợp cho liên kết này."}), 400

            return jsonify(response_data), 200

    except Exception as e:
        return jsonify({"error": f"Không thể xử lý video: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)