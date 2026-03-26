# 🚗 Vehicle Survey & Tracking System

A **production-ready, browser-based** real-time vehicle survey system with intelligent detection, tracking, and automated logging. Works on both laptop and mobile devices.

## ✨ Key Features

- **📱 Browser Camera Access**: Real-time camera streaming from laptop webcams or mobile phone cameras
- **🎯 AI-Powered Detection**: YOLOv8 object detection for vehicles (cars, trucks, bikes, buses, vans)
- **🔄 Smart Tracking**: ByteTrack multi-object tracking with stable IDs
- **🧭 Direction Analysis**: Configurable zone-based direction detection (North/South/East/West)
- **📊 Live Dashboard**: Real-time counts, FPS, and vehicle statistics
- **📋 Google Sheets Logging**: Automatic event logging with de-duplication
- **💾 Offline Backup**: SQLite caching and CSV export fallback
- **🎨 Professional UI**: Clean, responsive design for mobile and desktop
- **⚡ Performance Optimized**: Lightweight models for stable laptop/mobile performance

## 🚀 Quick Start

### 1. Install Dependencies
```bash
cd vehicle_survey_web
python3 -m pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your settings (Google Sheets optional for demo)
```

### 3. Run the Application
```bash
# Option 1: Use the run script
./run.sh

# Option 2: Manual start
python3 app.py
```

### 4. Open in Browser
- **URL**: http://localhost:8000
- **Mobile**: Access from your phone's browser on the same network

## 📋 How to Use

1. **Open the website** in your browser
2. **Click "Start Survey"** - Camera permission will be requested
3. **Allow camera access** - Live video feed starts
4. **Position camera** to view vehicles moving through zones
5. **Watch real-time detection** - Bounding boxes, tracking IDs, and directions appear
6. **Monitor the dashboard** - Live counts and statistics update
7. **Click "Stop Survey"** when done - Session summary and CSV export generated

## ⚙️ Configuration

### Camera & Direction Zones
Edit `config/cameras.example.json` to configure:
- Zone polygons (normalized coordinates 0.0-1.0)
- Direction rules (entry→exit zone mappings)
- Camera-specific settings

### Environment Variables (.env)
```bash
# Server
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000

# AI Model
YOLO_MODEL=yolov8n.pt          # Lightweight model for performance
YOLO_CONF=0.40                 # Detection confidence threshold
YOLO_IOU=0.50                  # NMS IoU threshold

# Processing
PROC_WIDTH=640                 # Frame processing width
PROC_HEIGHT=360                # Frame processing height

# Direction Logic
CAMERA_DIRECTION_CONFIG_PATH=config/cameras.example.json

# Storage
SNAPSHOT_DIR=logs/snapshots    # Vehicle snapshot storage
SQLITE_PATH=logs/events_cache.db  # Offline event cache

# Google Sheets (Optional)
GOOGLE_SHEETS_ENABLED=false    # Set to true to enable
GOOGLE_SERVICE_ACCOUNT_JSON=secrets/service_account.json
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
```

## 🔧 Google Sheets Setup (Optional)

1. **Create Google Cloud Project**
2. **Enable Google Sheets API**
3. **Create Service Account** and download JSON key
4. **Share your spreadsheet** with the service account email
5. **Update .env** with your spreadsheet ID and service account path

The system will automatically create required sheets and headers.

## 📊 Data Format

### Raw Events Sheet
| Timestamp | Session ID | Track ID | Vehicle Type | Direction | Confidence | Camera Type | Camera Name | Entry Zone | Exit Zone | Frame Number | Snapshot Path | Event ID |
|-----------|------------|----------|--------------|-----------|------------|-------------|-------------|------------|-----------|--------------|---------------|----------|
| 2024-01-01 12:00:00 UTC | uuid... | 1 | car | North→South | 0.85 | laptop_front | FrontCam | North | South | 123 | snapshots/car_001.jpg | sha256... |

### Vehicle Types Detected
- `bike` - motorcycles and bicycles
- `car` - passenger vehicles
- `van` - vans and SUVs (aspect ratio > 1.8)
- `truck` - trucks and large vehicles
- `bus` - buses
- `other vehicle` - unrecognized vehicle types

## 📱 Mobile vs Laptop Usage

### Laptop/Desktop
- Uses default webcam with `facingMode: "user"` (front camera preferred)
- Higher processing resolution possible
- Better performance for longer sessions

### Mobile Phone
- Uses back camera with `facingMode: "environment"`
- Optimized for mobile browsers
- Touch-friendly interface
- Same network access for testing

## 🛠️ Architecture

```
Browser (Frontend)
├── HTML/CSS/JS UI
├── Camera Access (getUserMedia)
├── WebSocket Streaming
└── Real-time Overlay

Backend (Python/FastAPI)
├── WebSocket Frame Processing
├── YOLO Detection + ByteTrack
├── Direction Classification
├── Google Sheets Logger
└── SQLite Cache

Data Flow
├── Browser → JPEG frames → Backend
├── Backend → Detection → Tracking → Direction
├── Backend → Events → Google Sheets + SQLite
└── Backend → Updates → Browser Overlay
```

## 🔍 Troubleshooting

### Camera Issues
- **Permission denied**: Allow camera access in browser settings
- **No camera found**: Check camera connections/permissions
- **Mobile camera**: Ensure back camera access, try different browsers

### Performance Issues
- **Low FPS**: Reduce `PROC_WIDTH/PROC_HEIGHT` in .env
- **High CPU**: Use lighter model (`yolov8n.pt`) or lower `YOLO_CONF`
- **Browser lag**: Close other tabs, use Chrome/Firefox

### Google Sheets Issues
- **Auth errors**: Verify service account JSON and spreadsheet sharing
- **Network errors**: Events cached locally, will retry on next session

## 📈 Performance Tips

- **Model Selection**: `yolov8n.pt` (fastest) → `yolov8s.pt` (balanced) → `yolov8m.pt` (accurate)
- **Frame Rate**: 8-15 FPS optimal for real-time feel
- **Resolution**: 640x360 works well on most devices
- **Zone Configuration**: Keep zones simple for reliable direction detection

## 🚀 Deployment

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run with auto-reload
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### Production Server
```bash
# Use production ASGI server
pip install gunicorn uvicorn[standard]
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Docker (Optional)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "app.py"]
```

## 📝 API Reference

### REST Endpoints
- `GET /` - Main application UI
- `POST /api/session/start` - Start survey session
- `POST /api/session/stop` - Stop session and get summary

### WebSocket
- `ws://localhost:8000/ws?sessionId={id}` - Real-time frame processing

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly (camera access, mobile compatibility)
5. Submit a pull request

## 📄 License

This project is open source. See LICENSE file for details.

## 🙏 Acknowledgments

- **Ultralytics YOLO** for object detection
- **ByteTrack** for multi-object tracking
- **FastAPI** for the web framework
- **OpenCV** for computer vision utilities

---

**Ready to survey vehicles?** Click "Start Survey" and let the AI do the counting! 🚗📊

## How it works (product-grade data flow)
1. You open `http://localhost:8000`
2. Click **Start Survey**
3. Your browser requests camera permission via `getUserMedia`
4. Frames are sampled from the live preview, JPEG-compressed, and streamed to the backend over WebSocket
5. The backend runs detection + tracking + direction logic and returns overlay metadata to the frontend
6. Confirmed vehicle events are appended to Google Sheets (and cached to SQLite if Sheets fails)
7. Click **Stop Survey** anytime to stop streaming and generate an offline CSV export

## Requirements
- Python 3.9+
- A camera device (laptop webcam / phone camera)
- Google Sheets service account (for logging)

## Setup

### 1. Install backend dependencies
```bash
cd vehicle_survey_web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables
```bash
cp .env.example .env
```

Edit `.env`:
- `GOOGLE_SERVICE_ACCOUNT_JSON`: path to your service account JSON
- `GOOGLE_SHEETS_SPREADSHEET_ID`: target spreadsheet ID

### 3. Direction zones calibration
Edit `config/cameras.example.json`:
- `points_normalized` values are in `[0..1]` relative to the processing frame (`PROC_WIDTH/PROC_HEIGHT`)
- `direction_rules` map `entry_zone -> exit_zone` to a direction label (ex: `North->South`)

### 4. Google Sheets setup (service account)
1. Create a Google Cloud Service Account
2. Download the JSON key
3. Share your spreadsheet with the service account email
4. The app auto-creates headers if the sheet is empty

Expected raw sheet headers:
`Timestamp, Session ID, Track ID, Vehicle Type, Direction, Confidence, Camera Type, Camera Name, Entry Zone, Exit Zone, Frame Number, Snapshot Path, Event ID`

## Run locally
Start the server:
```bash
cd vehicle_survey_web
source .venv/bin/activate
python app.py
```

Open:
- `http://localhost:8000`

## Camera access behavior (laptop vs mobile)
- **Laptop**: uses the default webcam with `facingMode: "user"` (best-effort for front camera)
- **Phone**: uses the back camera with `facingMode: "environment"`
- If permission is denied, you’ll see a clear UI error and the session will be stopped safely.
- Stop Survey stops the WebSocket stream and releases camera tracks without refreshing the page.

## Performance notes
- The frontend streams downscaled JPEG frames to keep laptop FPS stable.
- Default processing resolution is `PROC_WIDTH=640`, `PROC_HEIGHT=360`.
- Detection runs on the backend; if you need more accuracy, increase `YOLO_CONF`/`YOLO_IOU` or use a bigger model (e.g., `yolov8s.pt`).

## Optional backup/export
- If Google Sheets is unavailable, events are cached in `logs/events_cache.db`
- On Stop Survey, a CSV export is written to `exports/session_<sessionId>.csv`

## Sample event row format (RawLogs sheet)
- `Timestamp`: UTC time string
- `Session ID`: UUID per run
- `Track ID`: stable tracker id
- `Vehicle Type`: `bike | car | truck | van | bus | other vehicle`
- `Direction`: `North->South`, `East->West`, etc.
- `Confidence`: blended confidence of detection + direction confirmation
- `Camera Type`: `laptop_front | mobile_back`
- `Camera Name`: configured camera name (default `FrontCam`)
- `Entry Zone / Exit Zone`: zone names from your calibration JSON
- `Frame Number`: sequential frame counter from the session
- `Snapshot Path`: local cropped snapshot path (if write succeeded)
- `Event ID`: deterministic hash used to avoid duplicates

