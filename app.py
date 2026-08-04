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
import shutil

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


@app.route('/api/download/video', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def download_video():
    url = request.args.get('url') if request.method == 'GET' else request.form.get('url')
    format_id = request.args.get('format_id', 'best') if request.method == 'GET' else request.form.get('format_id', 'best')
    expected_ext = request.args.get('ext', 'mp4') if request.method == 'GET' else request.form.get('ext', 'mp4')

    if not url:
        return jsonify({"error": "Thiếu URL video"}), 400

    q = queue.Queue()

    def progress_hook(d):
        if d['status'] == 'downloading':
            p_clean = re.sub(r'\x1b[^m]*m', '', d.get('_percent_str', '0%')).strip().replace('%', '')
            try: q.put(f"data: [PROGRESS] {float(p_clean)}\n\n")
            except ValueError: pass
        elif d['status'] == 'finished':
            q.put(f"data: [LOG] Đang xử lý và hợp nhất các phân mảnh...\n\n")

    def run_download_thread():
        check_and_cleanup_storage()
        is_tiktok = 'tiktok.com' in url or 'douyin.com' in url
        is_facebook = 'facebook.com' in url or 'fb.watch' in url

        try:
            if is_tiktok:
                output_filename = process_tiktok_download(url, expected_ext, is_audio_only=False)
                q.put(f"DONE:{os.path.basename(output_filename)}")
            elif is_facebook:
                q.put(f"data: [LOG] Đang xử lý video Facebook...\n\n")
                ydl_opts = {
                    'format': 'best',
                    'merge_output_format': expected_ext,
                    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
                    'progress_hooks': [progress_hook],
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                        'Accept-Language': 'en-us,en;q=0.5',
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    output_filename = os.path.splitext(filename)[0] + f'.{expected_ext}'
                    q.put(f"DONE:{os.path.basename(output_filename)}")
            else:
                ydl_format = f"{format_id}+bestaudio/best" if format_id != 'best' else 'bestvideo+bestaudio/best'
                ydl_opts = {
                    'format': ydl_format,
                    'merge_output_format': expected_ext,
                    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
                    'postprocessor_args': ['-threads', '1'],
                    'progress_hooks': [progress_hook],
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': expected_ext, 
                    }],
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                        'Accept-Language': 'en-us,en;q=0.5',
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    output_filename = os.path.splitext(filename)[0] + f'.{expected_ext}'
                    q.put(f"DONE:{os.path.basename(output_filename)}")
        except Exception as e:
            q.put(f"ERROR:{str(e)}")

    def generate_logs():
        yield ":" + (" " * 2048) + "\n\n"
        yield f"data: [LOG] Khởi tạo luồng xử lý VIDEO...\n\n"
        threading.Thread(target=run_download_thread).start()
        
        ping_count = 0
        while True:
            try:
                msg = q.get(timeout=1)
                if msg.startswith("DONE:"):
                    yield f"data: [PROGRESS] 100\n\ndata: [SUCCESS] {msg.split(':', 1)[1]}\n\n"
                    break
                elif msg.startswith("ERROR:"):
                    yield f"data: [ERROR] {msg.split(':', 1)[1]}\n\n"
                    break
                else: yield msg
            except queue.Empty:
                yield ":ping\n\n"
                ping_count += 1
                if ping_count >= 10:
                    yield f"data: [LOG] Đang xử lý Video... (Vui lòng không đóng trang)\n\n"
                    ping_count = 0

    headers = {'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
    return Response(generate_logs(), mimetype='text/event-stream', headers=headers)


@app.route('/api/download/audio', methods=['GET', 'POST'])
@limiter.limit("3 per minute")
def download_audio():
    url = request.args.get('url') if request.method == 'GET' else request.form.get('url')
    expected_ext = request.args.get('ext', 'mp3') if request.method == 'GET' else request.form.get('ext', 'mp3')

    if not url:
        return jsonify({"error": "Thiếu URL âm thanh"}), 400

    q = queue.Queue()

    def progress_hook(d):
        if d['status'] == 'downloading':
            p_clean = re.sub(r'\x1b[^m]*m', '', d.get('_percent_str', '0%')).strip().replace('%', '')
            try: q.put(f"data: [PROGRESS] {float(p_clean)}\n\n")
            except ValueError: pass
        elif d['status'] == 'finished':
            q.put(f"data: [LOG] Tải xuống hoàn tất, chuẩn bị bóc tách {expected_ext.upper()}...\n\n")

    def run_download_thread():
        check_and_cleanup_storage()
        
        # Mở rộng nhận diện cho cả TikTok và Facebook
        is_tiktok = 'tiktok.com' in url or 'douyin.com' in url
        is_facebook = 'facebook.com' in url or 'fb.watch' in url
        is_social = is_tiktok or is_facebook

        try:
            if is_tiktok:
                q.put(f"data: [LOG] Kích hoạt TikTok Engine bóc tách âm thanh...\n\n")
                output_filename = process_tiktok_download(url, expected_ext, is_audio_only=True)
                q.put(f"DONE:{os.path.basename(output_filename)}")
            elif is_facebook:
                q.put(f"data: [LOG] Kích hoạt cơ chế an toàn cho Facebook Audio...\n\n")
                ydl_opts = {
                    'format': 'best',
                    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
                    'progress_hooks': [progress_hook],
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    final_filename = ydl.prepare_filename(info)
                    
                    # Bóc tách thủ công bằng FFmpeg thô tránh lỗi phân tích cú pháp audio của Facebook
                    output_filename = os.path.splitext(final_filename)[0] + f'.{expected_ext}'
                    audio_codec = 'libmp3lame' if expected_ext == 'mp3' else 'aac'
                    try:
                        subprocess.run([
                            ffmpeg_exe, '-y', '-i', final_filename,
                            '-vn', '-acodec', audio_codec, '-b:a', '192k',
                            '-threads', '1', output_filename
                        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        
                        if os.path.exists(final_filename) and final_filename != output_filename:
                            os.remove(final_filename)
                    except subprocess.CalledProcessError:
                        pass

                q.put(f"DONE:{os.path.basename(output_filename)}")
            else:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
                    'postprocessor_args': ['-threads', '1'],
                    'progress_hooks': [progress_hook],
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': expected_ext,
                        'preferredquality': '192',
                    }],
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                    }
                }
                q.put(f"data: [LOG] Đang chuyển đổi sang định dạng {expected_ext.upper()} (Chạy ngầm 1 luồng)...\n\n")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    final_filename = ydl.prepare_filename(info)
                    output_filename = os.path.splitext(final_filename)[0] + f'.{expected_ext}'
                    q.put(f"DONE:{os.path.basename(output_filename)}")
        except Exception as e:
            q.put(f"ERROR:{str(e)}")

    def generate_logs():
        yield ":" + (" " * 2048) + "\n\n"
        yield f"data: [LOG] Khởi tạo luồng xử lý AUDIO...\n\n"
        threading.Thread(target=run_download_thread).start()
        
        ping_count = 0
        while True:
            try:
                msg = q.get(timeout=1)
                if msg.startswith("DONE:"):
                    yield f"data: [PROGRESS] 100\n\ndata: [SUCCESS] {msg.split(':', 1)[1]}\n\n"
                    break
                elif msg.startswith("ERROR:"):
                    yield f"data: [ERROR] {msg.split(':', 1)[1]}\n\n"
                    break
                else: yield msg
            except queue.Empty:
                yield ":ping\n\n"
                ping_count += 1
                if ping_count >= 10:
                    yield f"data: [LOG] Đang xử lý Audio... (Vui lòng không đóng trang)\n\n"
                    ping_count = 0

    headers = {'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
    return Response(generate_logs(), mimetype='text/event-stream', headers=headers)

# Ngưỡng giới hạn ổ cứng cho phép: 100MB (vì ổ cứng trên Free tier rất nhỏ)
MAX_ALLOWED_FOLDER_SIZE = 100 * 1024 * 1024 

def check_and_cleanup_storage():
    """Kiểm tra tổng dung lượng thư mục downloads, nếu quá 100MB sẽ xóa bớt file cũ"""
    try:
        if not os.path.exists(DOWNLOAD_DIR):
            return

        total_size = 0
        file_list = []

        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                file_size = os.path.getsize(file_path)
                file_mtime = os.path.getmtime(file_path)
                total_size += file_size
                file_list.append({'path': file_path, 'mtime': file_mtime, 'size': file_size})

        if total_size > MAX_ALLOWED_FOLDER_SIZE:
            print(f"[Storage Warning] Thư mục downloads vượt quá 100MB. Đang dọn dẹp...")
            file_list.sort(key=lambda x: x['mtime'])

            for file_info in file_list:
                if total_size <= MAX_ALLOWED_FOLDER_SIZE:
                    break
                try:
                    os.remove(file_info['path'])
                    total_size -= file_info['size']
                    print(f"[Auto-Clean] Đã xóa file cũ: {os.path.basename(file_info['path'])}")
                except Exception as ex:
                    print(f"[Auto-Clean Error]: {ex}")
    except Exception as e:
        print(f"[Storage Check Error]: {e}")
        
def process_tiktok_download(url, expected_ext, is_audio_only=False):
    """
    TikTok Engine độc lập: Thực hiện chiến lược 3 giai đoạn dự phòng 
    nhằm đảm bảo luôn lấy được video/audio hoàn chỉnh kèm theo kiểm định ffprobe.
    """
    ydl_opts_base = {
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': ffmpeg_exe if ffmpeg_exe else None,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
        }
    }

    # Bước trích xuất danh sách formats để phân tích 3 nhóm
    try:
        with yt_dlp.YoutubeDL(ydl_opts_base) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
    except Exception:
        formats = []

    # Phân loại 3 nhóm: muxed (có sẵn hình+tiếng), video-only, audio-only
    muxed_list = []
    v_only_list = []
    a_only_list = []

    for f in formats:
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        f_url = f.get('url')
        if not f_url:
            continue
        
        if vcodec != 'none' and acodec != 'none':
            muxed_list.append(f)
        elif vcodec != 'none' and acodec == 'none':
            v_only_list.append(f)
        elif vcodec == 'none' and acodec != 'none':
            a_only_list.append(f)

    # Sắp xếp ưu tiên độ phân giải hoặc bitrate cao nhất
    muxed_list.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
    v_only_list.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
    a_only_list.sort(key=lambda x: x.get('abr', 0) or 0, reverse=True)

    def has_audio_stream(file_path):
        """Sử dụng ffprobe kiểm tra xem file có thực sự chứa luồng audio hay không"""
        if not ffmpeg_exe:
            return True # Nếu không có ffprobe thì mặc định chấp nhận
        # Tìm ffprobe bằng đường dẫn tương ứng với ffmpeg_exe
        ffprobe_exe = ffmpeg_exe.replace('ffmpeg', 'ffprobe')
        if not os.path.exists(ffprobe_exe):
            # Thử tìm trong hệ thống nếu không cùng thư mục
            ffprobe_exe = shutil.which('ffprobe') or 'ffprobe'
        
        try:
            cmd = [ffprobe_exe, '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            return 'audio' in result.stdout
        except Exception:
            return True # Tránh ngắt quãng nếu lỗi tiến trình phụ

    downloaded_file = None

    # --- CHỨC NĂNG TẢI AUDIO TIKTOK ---
    if is_audio_only:
        # Giai đoạn 1: Thử tải trực tiếp stream audio tốt nhất nếu có
        if a_only_list:
            best_audio_format = a_only_list[0]['format_id']
            opts = dict(ydl_opts_base)
            opts['format'] = best_audio_format
            opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    inf = ydl.extract_info(url, download=True)
                    downloaded_file = ydl.prepare_filename(inf)
            except Exception:
                pass

        # Giai đoạn 2 (Fallback): Tải video gộp hoặc video-only rồi dùng ffmpeg bóc tách sang Audio
        if not downloaded_file:
            fallback_format = muxed_list[0]['format_id'] if muxed_list else 'best'
            opts = dict(ydl_opts_base)
            opts['format'] = fallback_format
            opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    inf = ydl.extract_info(url, download=True)
                    downloaded_file = ydl.prepare_filename(inf)
            except Exception as e:
                raise Exception(f"Không thể tải nguồn TikTok để bóc tách audio: {str(e)}")

        # Tiến hành chuyển đổi sang định dạng Audio yêu cầu (mp3, m4a...) bằng FFmpeg thô
        if downloaded_file and os.path.exists(downloaded_file):
            output_audio = os.path.splitext(downloaded_file)[0] + f'.{expected_ext}'
            audio_codec = 'libmp3lame' if expected_ext == 'mp3' else 'aac'
            try:
                subprocess.run([
                    ffmpeg_exe, '-y', '-i', downloaded_file,
                    '-vn', '-acodec', audio_codec, '-b:a', '192k',
                    '-threads', '1', output_audio
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if downloaded_file != output_audio and os.path.exists(downloaded_file):
                    os.remove(downloaded_file)
                return output_audio
            except Exception as e:
                raise Exception(f"Lỗi bóc tách audio TikTok: {str(e)}")
        
        raise Exception("Không thể hoàn tất tải audio TikTok.")

    # --- CHỨC NĂNG TẢI VIDEO TIKTOK (Chiến lược 3 Giai đoạn) ---
    # Giai đoạn 1: Ưu tiên chọn video muxed (có sẵn hình + tiếng) cao nhất
    if muxed_list and not downloaded_file:
        target_fmt = muxed_list[0]['format_id']
        opts = dict(ydl_opts_base)
        opts['format'] = target_fmt
        opts['merge_output_format'] = expected_ext
        opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                inf = ydl.extract_info(url, download=True)
                temp_file = ydl.prepare_filename(inf)
                base_ext_file = os.path.splitext(temp_file)[0] + f'.{expected_ext}'
                if os.path.exists(base_ext_file):
                    temp_file = base_ext_file
                
                if os.path.exists(temp_file) and has_audio_stream(temp_file):
                    downloaded_file = temp_file
        except Exception:
            pass

    # Giai đoạn 2: Nếu Giai đoạn 1 không thành công hoặc file thiếu tiếng, kết hợp video-only + audio-only
    if not downloaded_file and v_only_list and a_only_list:
        v_fmt = v_only_list[0]['format_id']
        a_fmt = a_only_list[0]['format_id']
        opts = dict(ydl_opts_base)
        opts['format'] = f"{v_fmt}+{a_fmt}"
        opts['merge_output_format'] = expected_ext
        opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                inf = ydl.extract_info(url, download=True)
                temp_file = ydl.prepare_filename(inf)
                base_ext_file = os.path.splitext(temp_file)[0] + f'.{expected_ext}'
                if os.path.exists(base_ext_file):
                    temp_file = base_ext_file

                if os.path.exists(temp_file) and has_audio_stream(temp_file):
                    downloaded_file = temp_file
        except Exception:
            pass

    # Giai đoạn 3: Fallback cuối cùng thử các chuỗi format dự phòng (bestvideo+bestaudio, best...)
    if not downloaded_file:
        fallbacks = ['bestvideo+bestaudio/best', 'bv*+ba/b', 'best']
        for fb in fallbacks:
            opts = dict(ydl_opts_base)
            opts['format'] = fb
            opts['merge_output_format'] = expected_ext
            opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    inf = yt_dlp.YoutubeDL(opts).extract_info(url, download=True)
                    temp_file = ydl.prepare_filename(inf)
                    base_ext_file = os.path.splitext(temp_file)[0] + f'.{expected_ext}'
                    if os.path.exists(base_ext_file):
                        temp_file = base_ext_file

                    if os.path.exists(temp_file):
                        if has_audio_stream(temp_file):
                            downloaded_file = temp_file
                            break
                        else:
                            # Nếu tải về mà không có tiếng thì xóa đi để thử chuỗi tiếp theo
                            os.remove(temp_file)
            except Exception:
                continue

    if not downloaded_file or not os.path.exists(downloaded_file):
        raise Exception("Không thể tải video TikTok có âm thanh sau khi thử mọi chiến lược dự phòng.")

    return downloaded_file

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)