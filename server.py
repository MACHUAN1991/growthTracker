import os
import sqlite3
import traceback
import struct
import subprocess
from datetime import datetime
from PIL import Image
import piexif
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='public')
CORS(app)

@app.errorhandler(Exception)
def handle_exception(e):
    print(f"ERROR: {str(e)}")
    print(traceback.format_exc())
    return jsonify({"error": str(e)}), 500

BASE_DIR = Path(__file__).parent
PHOTOS_DIR = BASE_DIR / "photos"
VIDEOS_DIR = BASE_DIR / "videos"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"
DB_PATH = BASE_DIR / "photos.db"

PHOTOS_DIR.mkdir(exist_ok=True)
VIDEOS_DIR.mkdir(exist_ok=True)
THUMBNAILS_DIR.mkdir(exist_ok=True)

def get_db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            original_name TEXT,
            taken_at DATETIME,
            description TEXT,
            file_type TEXT NOT NULL,
            latitude REAL,
            longitude REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS growth_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_date DATE NOT NULL,
            age_months REAL,
            height REAL,
            weight REAL,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_growth_date ON growth_records(record_date)")
    # 尝试添加坐标字段（兼容已有数据库）
    try:
        conn.execute("ALTER TABLE photos ADD COLUMN latitude REAL")
    except:
        pass
    try:
        conn.execute("ALTER TABLE photos ADD COLUMN longitude REAL")
    except:
        pass
    conn.commit()
    conn.close()

def get_photo_date(filepath):
    try:
        img = Image.open(filepath)
        exif_data = img._getexif()
        if exif_data:
            for tag, value in exif_data.items():
                if tag == 36867:  # DateTimeOriginal
                    try:
                        return datetime.strptime(value, '%Y:%m:%d %H:%M:%S').isoformat()
                    except:
                        pass
                elif tag == 36868:  # DateTimeDigitized
                    try:
                        return datetime.strptime(value, '%Y:%m:%d %H:%M:%S').isoformat()
                    except:
                        pass
        return datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
    except Exception as e:
        print(f"Error getting photo date: {e}")
        return datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

def get_video_date(filepath):
    """从 MP4/MOV 的 moov/mvhd 原子中提取真正的创建日期"""
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        # 查找 moov 原子
        moov_pos = data.find(b'moov')
        if moov_pos == -1:
            return datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
        moov_start = moov_pos
        # 解析 moov 子原子，找到 mvhd
        pos = moov_start + 4
        while pos < len(data) - 8:
            # 读取原子大小和类型
            atom_size = struct.unpack('>I', data[pos:pos+4])[0]
            atom_type = data[pos+4:pos+8]
            if atom_size < 8 or atom_size > len(data) - pos:
                break
            if atom_type == b'mvhd':
                # mvhd 结构: version(1) + flags(3) + creation_time(4) + modification_time(4) + timescale(4) + duration(4)
                mvhd_data = data[pos+8:pos+atom_size]
                version = mvhd_data[0]
                if version == 0:
                    creation_ts = struct.unpack('>I', mvhd_data[4:8])[0]
                else:
                    creation_ts = struct.unpack('>Q', mvhd_data[8:16])[0]
                # 转换: QuickTime epoch (1904) -> Unix epoch (1970)
                unix_ts = creation_ts - 2082844800
                # creation_ts=0 或负数说明元数据无效，直接用 mtime
                if unix_ts <= 0:
                    break
                return datetime.fromtimestamp(unix_ts).isoformat()
            pos += atom_size
        return datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
    except Exception as e:
        print(f"解析视频日期失败: {e}")
        return datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

def _to_decimal_dms(coord, ref):
    """将 ((度,度分母),(分,分分母),(秒,秒分母)) 转为十进制度"""
    try:
        degrees = coord[0][0] / coord[0][1]
        minutes = coord[1][0] / coord[1][1]
        seconds = coord[2][0] / coord[2][1]
    except (ZeroDivisionError, IndexError, TypeError, ValueError):
        return None
    decimal = degrees + minutes / 60 + seconds / 3600
    # ref 可能是 bytes 或 int
    if isinstance(ref, bytes):
        ref_char = ref[0:1]
    elif isinstance(ref, int):
        # 1=N/S, 3=E/W（IFD tag编号），但这里实际收到的是 tag 值，不是编号
        # GPSInteroperabilityTagRef: 78='N', 83='S', 69='E', 87='W'
        ref_char = bytes([ref]) if ref in (78, 83, 69, 87) else b'N'
    else:
        ref_char = b'N'
    if ref_char in (b'S', b'W'):
        decimal = -decimal
    return decimal

def _to_decimal_single(coord, ref):
    """将 (度小数, 分母) 格式或直接度数字转为十进制度"""
    try:
        if len(coord) == 1:
            decimal = coord[0][0] / coord[0][1]
        elif len(coord) == 2:
            # 可能是 (度, 度分母) 直接度格式
            decimal = coord[0][0] / coord[0][1]
        else:
            return None
    except (ZeroDivisionError, IndexError, TypeError, ValueError):
        return None
    if isinstance(ref, bytes):
        ref_char = ref[0:1]
    elif isinstance(ref, int):
        ref_char = bytes([ref]) if ref in (78, 83, 69, 87) else b'N'
    else:
        ref_char = b'N'
    if ref_char in (b'S', b'W'):
        decimal = -decimal
    return decimal

def get_photo_gps(filepath):
    """从照片 EXIF 中提取 GPS 坐标，返回 (latitude, longitude) 或 (None, None)"""
    try:
        exif_dict = piexif.load(str(filepath))
        gps = exif_dict.get('GPS', {})
        if not gps:
            return None, None

        # 尝试多种 tag key 格式（piexif 版本差异）
        lat = gps.get(2) or gps.get(b'2') or gps.get('GPSLatitude')
        lon = gps.get(4) or gps.get(b'4') or gps.get('GPSLongitude')
        lat_ref = gps.get(1) or gps.get(b'1') or gps.get('GPSLatitudeRef')
        lon_ref = gps.get(3) or gps.get(b'3') or gps.get('GPSLongitudeRef')

        if lat_ref is None:
            lat_ref = b'N'
        if lon_ref is None:
            lon_ref = b'E'

        if lat is None or lon is None:
            return None, None

        # 判断是 DMS 格式还是单个小数度格式
        lat_dec = None
        lon_dec = None

        # 纬度
        if isinstance(lat, tuple) and len(lat) >= 3:
            lat_dec = _to_decimal_dms(lat, lat_ref)
        elif isinstance(lat, tuple) and len(lat) == 1:
            lat_dec = _to_decimal_single(lat, lat_ref)
        elif isinstance(lat, (int, float)):
            lat_dec = float(lat)
            if isinstance(lat_ref, bytes):
                ref_char = lat_ref[0:1]
            elif isinstance(lat_ref, int):
                ref_char = bytes([lat_ref]) if lat_ref in (78, 83) else b'N'
            else:
                ref_char = b'N'
            if ref_char == b'S':
                lat_dec = -lat_dec

        # 经度
        if isinstance(lon, tuple) and len(lon) >= 3:
            lon_dec = _to_decimal_dms(lon, lon_ref)
        elif isinstance(lon, tuple) and len(lon) == 1:
            lon_dec = _to_decimal_single(lon, lon_ref)
        elif isinstance(lon, (int, float)):
            lon_dec = float(lon)
            if isinstance(lon_ref, bytes):
                ref_char = lon_ref[0:1]
            elif isinstance(lon_ref, int):
                ref_char = bytes([lon_ref]) if lon_ref in (69, 87) else b'E'
            else:
                ref_char = b'E'
            if ref_char == b'W':
                lon_dec = -lon_dec

        # 校验范围
        if lat_dec is None or lon_dec is None:
            return None, None
        if not (-90 <= lat_dec <= 90):
            return None, None
        if not (-180 <= lon_dec <= 180):
            return None, None

        return round(lat_dec, 6), round(lon_dec, 6)
    except Exception as e:
        print(f"Error getting GPS: {e}")
        return None, None

def generate_thumbnail(source_path, filename):
    try:
        img = Image.open(source_path)
        img.thumbnail((300, 300))
        thumbnail_path = THUMBNAILS_DIR / filename
        img.save(thumbnail_path, "JPEG")
    except Exception as e:
        print(f"Error generating thumbnail: {e}")

def is_dolby_vision(video_path):
    """检测视频是否为 Dolby Vision 编码"""
    try:
        with open(video_path, 'rb') as f:
            data = f.read(1024)
            return b'dby1' in data
    except Exception:
        return False

def generate_video_thumbnail(source_path, filename):
    """用 ffmpeg 提取视频第1秒的画面，添加播放图标，返回缩略图路径"""
    import subprocess
    import shutil

    thumbnail_path = THUMBNAILS_DIR / filename
    frame_path = THUMBNAILS_DIR / f"{filename}_frame.jpg"

    try:
        # 用 ffmpeg 提取第一帧画面
        cmd = [
            'ffmpeg', '-y',
            '-ss', '00:00:00',          # 第一帧（放-i前快速定位）
            '-i', str(source_path),
            '-loglevel', 'error',
            '-vframes', '1',
            '-q:v', '2',
            '-vf', 'scale=300:-1',
            '-update', '1',
            str(frame_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0 or not frame_path.exists():
            msg = result.stderr.decode()[:200]
            if is_dolby_vision(source_path):
                print(f"Skipping thumbnail for Dolby Vision: {filename}")
            else:
                print(f"ffmpeg failed: {msg}")
            return False

        # 添加播放图标
        img = Image.open(frame_path).convert("RGBA")
        W, H = img.size
        icon_size = max(W // 3, 80)
        icon_path = THUMBNAILS_DIR / "play_icon.png"

        # 如果没有播放图标，先创建一个
        if not icon_path.exists():
            create_play_icon(icon_path, icon_size)

        icon = Image.open(icon_path).convert("RGBA")
        icon = icon.resize((icon_size, icon_size), Image.LANCZOS)

        # 创建一个带透明层的画布
        composite = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        composite.paste(img, (0, 0))

        # 将播放图标放在中央，带半透明黑色圆形背景
        cx, cy = W // 2, H // 2
        bg_size = icon_size + 20
        bg = Image.new("RGBA", (bg_size, bg_size), (0, 0, 0, 0))
        from PIL.ImageDraw import ImageDraw
        draw = ImageDraw(bg)
        draw.ellipse([0, 0, bg_size, bg_size], fill=(0, 0, 0, 160))
        icon_paste_x = (bg_size - icon_size) // 2
        icon_paste_y = (bg_size - icon_size) // 2
        bg.paste(icon, (icon_paste_x, icon_paste_y), icon)

        paste_x = cx - bg_size // 2
        paste_y = cy - bg_size // 2
        composite.paste(bg, (paste_x, paste_y), bg)

        # 转为 JPEG 保存
        composite_rgb = composite.convert("RGB")
        composite_rgb.save(thumbnail_path, "JPEG", quality=85)

        # 清理临时帧文件
        frame_path.unlink(missing_ok=True)
        return True

    except subprocess.TimeoutExpired:
        if is_dolby_vision(source_path):
            print(f"Skipping thumbnail for Dolby Vision (timeout): {filename}")
        else:
            print(f"ffmpeg timeout: {filename}")
        return False
    except Exception as e:
        print(f"Error generating video thumbnail: {e}")
        return False

def create_play_icon(icon_path, size):
    """创建一个三角形播放图标"""
    from PIL.ImageDraw import ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw(img)
    # 画一个填充的三角形（稍微偏移使其居中）
    padding = size // 4
    points = [
        (padding + size // 6, padding),           # 左上
        (padding + size // 6, size - padding),    # 左下
        (size - padding, size // 2)               # 右边中点
    ]
    draw.polygon(points, fill=(255, 255, 255, 230))
    img.save(icon_path, "PNG")

def scan_photos():
    conn = get_db()
    cursor = conn.cursor()
    
    # 获取当前目录中的所有照片文件
    current_photos = set()
    for photo_file in PHOTOS_DIR.glob("*"):
        if photo_file.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            current_photos.add(photo_file.name)
    
    # 获取当前目录中的所有视频文件
    current_videos = set()
    for video_file in VIDEOS_DIR.glob("*"):
        if video_file.suffix.lower() in ['.mp4', '.mov', '.avi', '.mkv']:
            current_videos.add(video_file.name)
    
    # 获取数据库中的所有文件
    cursor.execute("SELECT filename, file_type FROM photos")
    db_files = cursor.fetchall()
    
    # 检测删除的文件
    for filename, file_type in db_files:
        if file_type == 'photo' and filename not in current_photos:
            # 删除数据库中的记录
            cursor.execute("DELETE FROM photos WHERE filename = ? AND file_type = 'photo'", (filename,))
        elif file_type == 'video' and filename not in current_videos:
            # 删除数据库中的记录
            cursor.execute("DELETE FROM photos WHERE filename = ? AND file_type = 'video'", (filename,))
    
    # 处理照片文件
    for photo_file in PHOTOS_DIR.glob("*"):
        if photo_file.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            cursor.execute("SELECT id FROM photos WHERE filename = ?", (photo_file.name,))
            if not cursor.fetchone():
                date_taken = get_photo_date(photo_file)
                lat, lon = get_photo_gps(photo_file)
                cursor.execute(
                    "INSERT INTO photos (filename, original_name, taken_at, file_type, latitude, longitude) VALUES (?, ?, ?, ?, ?, ?)",
                    (photo_file.name, photo_file.name, date_taken, 'photo', lat, lon)
                )
                generate_thumbnail(photo_file, photo_file.name)
            else:
                # 照片已在 DB，检查是否缺缩略图
                thumb_path = THUMBNAILS_DIR / photo_file.name
                if not thumb_path.exists():
                    generate_thumbnail(photo_file, photo_file.name)
    
    # 处理视频文件
    for video_file in VIDEOS_DIR.glob("*"):
        if video_file.suffix.lower() in ['.mp4', '.mov', '.avi', '.mkv']:
            cursor.execute("SELECT id FROM photos WHERE filename = ?", (video_file.name,))
            if not cursor.fetchone():
                # 对于视频，尝试从 MP4/MOV 元数据读取真实创建日期
                date_taken = get_video_date(video_file)
                cursor.execute(
                    "INSERT INTO photos (filename, original_name, taken_at, file_type) VALUES (?, ?, ?, ?)",
                    (video_file.name, video_file.name, date_taken, 'video')
                )
                # 为视频生成缩略图
                generate_video_thumbnail(video_file, video_file.name)
            else:
                # 视频已在 DB，检查是否缺缩略图
                thumb_path = THUMBNAILS_DIR / video_file.name
                if not thumb_path.exists():
                    generate_video_thumbnail(video_file, video_file.name)
    
    conn.commit()
    conn.close()

@app.route('/api/photos', methods=['GET'])
def list_photos():
    year = request.args.get('year')
    month = request.args.get('month')
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 500))
    file_type = request.args.get('type')

    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT id, filename, original_name, taken_at, description, file_type, latitude, longitude FROM photos"
    params = []
    where_clauses = []

    if year:
        where_clauses.append("strftime('%Y', taken_at) = ?")
        params.append(year)
    if month:
        where_clauses.append("strftime('%m', taken_at) = ?")
        params.append(month.zfill(2))
    if file_type:
        where_clauses.append("file_type = ?")
        params.append(file_type)

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    count_query = query.replace("SELECT id, filename, original_name, taken_at, description, file_type, latitude, longitude", "SELECT COUNT(*)")
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]

    query += " ORDER BY taken_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, (page - 1) * limit])

    cursor.execute(query, params)
    photos = []
    for row in cursor.fetchall():
        photos.append({
            "id": row[0],
            "filename": row[1],
            "original_name": row[2],
            "taken_at": row[3],
            "description": row[4],
            "file_type": row[5],
            "latitude": row[6],
            "longitude": row[7]
        })

    conn.close()
    return jsonify({"photos": photos, "total": total, "page": page, "limit": limit})

@app.route('/api/timeline', methods=['GET'])
def get_timeline():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            strftime('%Y', taken_at) as year,
            strftime('%m', taken_at) as month,
            COUNT(*) as count
        FROM photos
        GROUP BY year, month
        ORDER BY year DESC, month DESC
    """)
    timeline = []
    for row in cursor.fetchall():
        timeline.append({
            "year": row[0],
            "month": row[1],
            "count": row[2]
        })
    conn.close()
    return jsonify({"timeline": timeline})

@app.route('/api/photos/<int:photo_id>', methods=['GET'])
def get_photo(photo_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, filename, original_name, taken_at, description, file_type, latitude, longitude FROM photos WHERE id = ?",
        (photo_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Photo not found"}), 404
    return jsonify({
        "id": row[0],
        "filename": row[1],
        "original_name": row[2],
        "taken_at": row[3],
        "description": row[4],
        "file_type": row[5],
        "latitude": row[6],
        "longitude": row[7]
    })

@app.route('/api/photos/<int:photo_id>', methods=['PUT'])
def update_photo(photo_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    conn = get_db()
    cursor = conn.cursor()

    if 'description' in data:
        cursor.execute(
            "UPDATE photos SET description = ? WHERE id = ?",
            (data['description'], photo_id)
        )

    conn.commit()
    conn.close()
    return jsonify({"message": "Photo updated successfully"})

@app.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def delete_photo(photo_id):
    conn = get_db()
    cursor = conn.cursor()

    # 先获取文件信息
    cursor.execute("SELECT filename, file_type FROM photos WHERE id = ?", (photo_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Photo not found"}), 404

    filename, file_type = row

    # 从数据库删除
    cursor.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()

    # 删除实际文件
    if file_type == 'photo':
        photo_path = PHOTOS_DIR / filename
        thumb_path = THUMBNAILS_DIR / filename
        if photo_path.exists():
            photo_path.unlink()
        if thumb_path.exists():
            thumb_path.unlink()
    elif file_type == 'video':
        video_path = VIDEOS_DIR / filename
        thumb_path = THUMBNAILS_DIR / filename
        if video_path.exists():
            video_path.unlink()
        if thumb_path.exists():
            thumb_path.unlink()

    return jsonify({"message": "Deleted successfully"})

@app.route('/api/growth', methods=['GET'])
def list_growth():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, record_date, age_months, height, weight, notes, created_at FROM growth_records ORDER BY record_date ASC")
    records = []
    for row in cursor.fetchall():
        records.append({
            "id": row[0],
            "record_date": row[1],
            "age_months": row[2],
            "height": row[3],
            "weight": row[4],
            "notes": row[5],
            "created_at": row[6]
        })
    conn.close()
    return jsonify({"records": records})

@app.route('/api/growth', methods=['POST'])
def add_growth():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    record_date = data.get('record_date')
    height = data.get('height')
    weight = data.get('weight')
    age_months = data.get('age_months')
    notes = data.get('notes', '')
    if not record_date:
        return jsonify({"error": "record_date is required"}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO growth_records (record_date, age_months, height, weight, notes) VALUES (?, ?, ?, ?, ?)",
        (record_date, age_months, height, weight, notes)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({"message": "Record added", "id": new_id}), 201

@app.route('/api/growth/<int:record_id>', methods=['DELETE'])
def delete_growth(record_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM growth_records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Deleted successfully"})

@app.route('/api/photos/map', methods=['GET'])
def list_map_photos():
    year = request.args.get('year')
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT id, filename, original_name, taken_at, description, file_type, latitude, longitude FROM photos WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    params = []
    if year:
        query += " AND strftime('%Y', taken_at) = ?"
        params.append(year)
    query += " ORDER BY taken_at DESC"
    cursor.execute(query, params)
    photos = []
    for row in cursor.fetchall():
        photos.append({
            "id": row[0],
            "filename": row[1],
            "original_name": row[2],
            "taken_at": row[3],
            "description": row[4],
            "file_type": row[5],
            "latitude": row[6],
            "longitude": row[7]
        })
    conn.close()
    return jsonify({"photos": photos})

@app.route('/photos/<path:filename>')
def serve_photo(filename):
    return send_from_directory(PHOTOS_DIR, filename)

@app.route('/videos/<path:filename>')
def serve_video(filename):
    video_path = VIDEOS_DIR / filename
    if not video_path.exists():
        return "File not found", 404

    ext = video_path.suffix.lower()
    mime_types = {
        '.mp4': 'video/mp4',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo',
        '.mkv': 'video/x-matroska',
        '.webm': 'video/webm',
    }
    mime_type = mime_types.get(ext, 'application/octet-stream')

    file_size = video_path.stat().st_size
    range_header = request.headers.get('Range')

    if range_header:
        # 支持 range request（桌面浏览器播放视频需要）
        try:
            range_match = range_header.replace('bytes=', '').split('-')
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] else file_size - 1
        except ValueError:
            start, end = 0, file_size - 1

        length = end - start + 1
        with open(video_path, 'rb') as f:
            f.seek(start)
            data = f.read(length)

        resp = Response(data, status=206, mimetype=mime_type)
        resp.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        resp.headers['Accept-Ranges'] = 'bytes'
        resp.headers['Content-Length'] = length
        return resp
    else:
        # 无 range header 时直接返回整个文件
        return send_from_directory(VIDEOS_DIR, filename)

@app.route('/thumbnails/<path:filename>')
def serve_thumbnail(filename):
    return send_from_directory(THUMBNAILS_DIR, filename)

UPLOAD_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.avi', '.mkv'}
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv'}

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist('files')
    taken_at_override = request.form.get('taken_at')  # 可选：手动指定的拍摄日期
    uploaded = []
    errors = []
    converting_files = []

    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in UPLOAD_EXTENSIONS:
            errors.append(f"{f.filename}: 不支持的文件格式")
            continue

        # 处理文件名冲突
        dest_name = secure_filename(f.filename)
        if ext in PHOTO_EXTENSIONS:
            target_dir = PHOTOS_DIR
        else:
            target_dir = VIDEOS_DIR

        # 如果文件已存在，添加序号后缀
        save_path = target_dir / dest_name
        if save_path.exists():
            stem = Path(dest_name).stem
            counter = 1
            while save_path.exists():
                dest_name = f"{stem}_{counter}{ext}"
                save_path = target_dir / dest_name
                counter += 1

        f.save(save_path)

        # 确定拍摄日期：优先用用户指定的，否则从文件元数据提取
        if taken_at_override:
            date_taken = taken_at_override
        elif ext in PHOTO_EXTENSIONS:
            date_taken = get_photo_date(save_path)
        else:
            date_taken = get_video_date(save_path)
        file_type = 'photo' if ext in PHOTO_EXTENSIONS else 'video'

        # 提取 GPS 坐标（仅照片）
        lat, lon = (get_photo_gps(save_path) if ext in PHOTO_EXTENSIONS else (None, None))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO photos (filename, original_name, taken_at, file_type, latitude, longitude) VALUES (?, ?, ?, ?, ?, ?)",
                (dest_name, f.filename, date_taken, file_type, lat, lon)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            errors.append(f"{dest_name}: 文件已存在")
        finally:
            conn.close()

        # 生成缩略图
        if ext in PHOTO_EXTENSIONS:
            generate_thumbnail(save_path, dest_name)
        else:
            generate_video_thumbnail(save_path, dest_name)

        uploaded.append(dest_name)

    return jsonify({
        "uploaded": uploaded,
        "errors": errors,
        "total": len(uploaded),
        "converting": converting_files
    })

@app.route('/')
def index():
    return send_file(BASE_DIR / "public" / "index.html")

@app.route('/growth')
def growth():
    return send_file(BASE_DIR / "public" / "growth.html")

@app.route('/map')
def map_view():
    return send_file(BASE_DIR / "public" / "map.html")

@app.route('/test')
def test():
    return send_file(BASE_DIR / "public" / "test.html")

if __name__ == '__main__':
    init_db()
    scan_photos()
    print("Server starting on http://localhost:8000")
    app.run(host='0.0.0.0', port=8000, debug=True)
else:
    # gunicorn 或其他 wsgi 服务器加载时也执行初始化
    init_db()
    scan_photos()