from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

@app.route('/api/extract-info', methods=['POST'])
def extract_info():
    url = None
    
    # 1. Ép đọc dữ liệu thô (raw request data) trước tiên bất kể header là gì
    try:
        if request.data:
            import json
            raw_body = request.data.decode('utf-8')
            parsed_data = json.loads(raw_body)
            url = parsed_data.get('url')
    except Exception:
        pass

    # 2. Dự phòng dùng request.get_json() chuẩn của Flask
    if not url:
        try:
            data = request.get_json(silent=True)
            if data and isinstance(data, dict):
                url = data.get('url')
        except Exception:
            pass

    # 3. Dự phòng cuối cùng dùng form-data
    if not url and request.form:
        url = request.form.get('url')

    # Kiểm tra an toàn
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

            # Khai báo các độ phân giải mục tiêu muốn lấy
            target_resolutions = ['1080p', '720p', '480p']
            seen_resolutions = set()
            audio_added = False

            # Duyệt ngược danh sách để ưu tiên các file chất lượng cao hơn (thường nằm cuối list)
            for f in reversed(info.get('formats', [])):
                ext = f.get('ext')
                vcodec = f.get('vcodec')
                acodec = f.get('acodec')
                format_note = f.get('format_note')
                
                # Bỏ qua nếu không có URL tải
                if not f.get('url'):
                    continue

                # 1. LỌC ĐỊNH DẠNG AUDIO (Ưu tiên m4a/mp3)
                if vcodec == 'none' and acodec != 'none' and not audio_added:
                    response_data["formats"].append({
                        "format_id": f.get('format_id'),
                        "resolution": "Audio High Quality",
                        "ext": ext,
                        "url": f.get('url'),
                        "filesize": f.get('filesize')
                    })
                    audio_added = True
                    continue # Xử lý xong audio thì chuyển qua file tiếp theo

                # 2. LỌC ĐỊNH DẠNG VIDEO CÓ CẢ HÌNH VÀ TIẾNG (Tiêu chuẩn: mp4, có acodec)
                if ext == 'mp4' and vcodec != 'none' and acodec != 'none':
                    # Lấy độ phân giải (VD: '1080p', '720p')
                    # format_note thường chứa "1080p", nếu không có thì lấy height (VD: 1080 -> 1080p)
                    res_label = format_note if format_note else f"{f.get('height')}p" if f.get('height') else None
                    
                    if res_label:
                        # Chuẩn hóa nhãn độ phân giải (VD: "1080p60" -> "1080p") để dễ so sánh
                        clean_res = res_label.split('60')[0].split('50')[0] if 'p' in res_label else res_label

                        # Nếu độ phân giải nằm trong mục tiêu VÀ chưa được thêm vào mảng
                        if clean_res in target_resolutions and clean_res not in seen_resolutions:
                            response_data["formats"].append({
                                "format_id": f.get('format_id'),
                                "resolution": res_label,
                                "ext": ext,
                                "url": f.get('url'),
                                "filesize": f.get('filesize')
                            })
                            seen_resolutions.add(clean_res)

            # Sắp xếp lại danh sách: Audio cuối cùng, Video giảm dần độ phân giải
            # (Phần này có thể tùy chỉnh thêm nếu cần, hiện tại danh sách đã khá sạch)
            
            return jsonify(response_data), 200

    except Exception as e:
        return jsonify({"error": f"Không thể xử lý video: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)