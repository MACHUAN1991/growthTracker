# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Flask-based photo gallery website "成长记录网站" for recording child's growth moments with photos and videos. Features timeline view, auto-generated thumbnails, EXIF GPS extraction, and responsive lazy loading.

## Tech Stack

- Python 3 / Flask 3.0.0
- SQLite database
- PIL/Pillow 10.2.0 for image processing
- piexif for EXIF/GPS extraction
- flask-cors for CORS support
- Optional: ffmpeg for video thumbnail generation

## Running

```bash
pip install -r requirements.txt
python server.py
# Access at http://localhost:8000
```

## Key Files

- `server.py` - Main Flask app (~660 lines), all routes and business logic
- `sync.py` - Deploy script to remote server 47.93.237.75 via SSH/SFTP
- `check_db.py` - Quick DB statistics
- `gen_thumb_server.py` - Standalone video thumbnail generator (used on server)

## Directory Structure

```
photos/        - Original photos (JPG, PNG, GIF, WebP)
videos/        - Original videos (MP4, MOV, AVI, MKV)
thumbnails/    - Auto-generated thumbnails (300x300, JPEG)
photos.db      - SQLite database
public/        - Static HTML frontend
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/photos` | List photos (supports year, month, type, page, limit) |
| GET | `/api/timeline` | Get year/month groupings with counts |
| GET | `/api/photos/<id>` | Get single photo details |
| PUT | `/api/photos/<id>` | Update photo description |
| DELETE | `/api/photos/<id>` | Delete photo and files |
| POST | `/api/upload` | Upload photos/videos |
| GET | `/photos/<filename>` | Serve original photo |
| GET | `/videos/<filename>` | Serve video |
| GET | `/thumbnails/<filename>` | Serve thumbnail |

## EXIF/GPS Handling

- Photo date: extracts `DateTimeOriginal` (tag 36867) or `DateTimeDigitized` (tag 36868) from EXIF
- GPS coordinates: extracts latitude/longitude from GPS IFD, supports multiple tag key formats (int, bytes, string)
- Video date: parses MP4/MOV `moov/mvhd` atom for creation timestamp (QuickTime epoch 1904 → Unix epoch)
- GPS stored as `latitude`, `longitude` columns in photos table

## Thumbnail Generation

- Photos: PIL thumbnail (300x300, JPEG)
- Videos: ffmpeg extracts frame at 1 second, composites play icon, saves as JPEG
- Thumbnails auto-regenerated on startup if missing

## Deployment

`sync.py` deploys `server.py`, `requirements.txt`, and `public/` to `/var/www/photo_gal` on 47.93.237.75, then runs `systemctl restart photo_gal`.
