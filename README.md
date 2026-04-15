# PlantDiseases

AI-powered houseplant disease detection app for Android with a Python ML server.

---

## Architecture Overview

```
┌──────────────────────┐         ┌──────────────────────────┐
│   Android App        │  HTTP   │   Python Server          │
│   (Kotlin)           │ ──────► │   (FastAPI)              │
│                      │         │                          │
│  • CameraX capture   │         │  • CNN model (MobileNet) │
│  • Room local DB     │  JSON   │  • PlantVillage dataset  │
│  • Retrofit client   │ ◄────── │  • Disease database      │
│  • Material 3 UI     │         │  • Image preprocessing   │
│  • RU/EN localization│         │  • Input validation      │
│  • Profile & stats   │         │  • Docker deployment     │
└──────────────────────┘         └──────────────────────────┘
```

---

## Android App

### Features
- **Camera scanner** (Photomath-style UI) — point at a plant leaf and capture
- **Image gallery picker** — analyze photos from device
- **Scan history** — all results saved locally in Room DB
- **Plant care guide** — 22 articles on diseases, pests, watering, lighting, care
- **Profile & statistics** — total scans, healthy/diseased ratio, most common issue
- **Share results** — export diagnosis as text
- **Low confidence warning** — alert when result is uncertain
- **Bilingual** — Russian / English with one-tap switching
- **Min SDK 26** (Android 8.0+)

### Tech Stack
| Component | Library |
|-----------|---------|
| Camera | CameraX 1.3 |
| Database | Room 2.6 |
| Network | Retrofit 2.9 + OkHttp 4 |
| Images | Glide 4.16 |
| Navigation | Jetpack Navigation |
| UI | Material Design 3 |
| Async | Kotlin Coroutines |

### Setup & Build

1. **Open in Android Studio** (Hedgehog 2023.1+ recommended)
   ```
   File → Open → select android/ folder
   ```

2. **Set server URL** in `app/build.gradle.kts`:
   ```kotlin
   // For emulator → localhost:
   buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000/\"")
   
   // For physical device on same WiFi:
   buildConfigField("String", "API_BASE_URL", "\"http://YOUR_PC_IP:8000/\"")
   ```

3. **Sync Gradle** and **Run** on device/emulator

### Project Structure
```
app/src/main/
├── java/com/plantdiseases/app/
│   ├── PlantDiseasesApp.kt          # Application class
│   ├── MainActivity.kt              # Main activity + nav
│   ├── data/
│   │   ├── local/Database.kt        # Room DB + DAO
│   │   ├── remote/ApiService.kt     # Retrofit API
│   │   ├── model/Models.kt          # Data models
│   │   ├── repository/ScanRepository.kt
│   │   └── GuideDataProvider.kt     # Offline guide articles (22 items)
│   ├── ui/
│   │   ├── camera/                  # Camera scanner screen
│   │   ├── gallery/                 # Scan history grid
│   │   ├── guide/                   # Care articles + detail
│   │   ├── profile/                 # Statistics & settings
│   │   ├── analysis/                # Loading/analyzing screen
│   │   └── result/                  # Disease diagnosis result
│   └── util/
│       ├── LocaleHelper.kt          # Language switching
│       └── ImageUtils.kt            # Image processing
└── res/
    ├── layout/                      # XML layouts
    ├── values/                      # English strings, colors, themes
    ├── values-ru/                   # Russian strings
    ├── drawable/                    # Icons & shapes
    ├── navigation/                  # Nav graph
    └── menu/                        # Bottom nav & toolbar menus
```

---

## Python Server

### Features
- **FastAPI** REST server with input validation
- **CNN model** — MobileNetV2 transfer learning
- **Demo mode** — works without trained model (uses color heuristics)
- **Bilingual responses** — all disease info in EN and RU
- **15 disease classes** including common houseplant problems
- **Docker** — ready for deployment
- **Request logging** — structured logs with timing

### Setup

1. **Create virtual environment:**
   ```bash
   cd server/
   python -m venv venv
   source venv/bin/activate   # Linux/Mac
   venv\Scripts\activate      # Windows
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run server (demo mode, no trained model needed):**
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```
   The server starts in **demo mode** and uses color-based heuristics.
   API docs: http://localhost:8000/docs

### Docker Deployment

```bash
cd server/
docker-compose up --build -d
```

### Training Your Own Model

1. **Download PlantVillage dataset:**
   - GitHub: https://github.com/spMohanty/PlantVillage-Dataset
   - Kaggle: https://www.kaggle.com/datasets/emmarex/plantdisease

2. **Organize data:**
   ```
   server/data/
   ├── train/
   │   ├── healthy/          # Images of healthy leaves
   │   ├── bacterial_spot/
   │   ├── early_blight/
   │   ├── powdery_mildew/
   │   └── ...
   └── val/
       ├── healthy/
       ├── bacterial_spot/
       └── ...
   ```

3. **Train:**
   ```bash
   python train.py
   ```
   Model saves to `models/plant_disease_model.h5`.
   Training uses MobileNetV2 with two-phase approach:
   - Phase 1: Train classifier head (15 epochs)
   - Phase 2: Fine-tune top 30 layers (10 epochs)
   - Outputs: per-class accuracy, confusion matrix, top confused pairs

4. **Restart server** — model loads automatically.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check + model status |
| `POST` | `/api/analyze` | Upload image → get diagnosis |

#### POST /api/analyze

**Request:** `multipart/form-data` with `image` field (JPEG, PNG, WebP, BMP; max 10 MB)

**Response:**
```json
{
  "disease_name": "Powdery Mildew",
  "disease_name_ru": "Мучнистая роса",
  "confidence": 0.87,
  "description": "...",
  "description_ru": "...",
  "treatment": ["Step 1", "Step 2"],
  "treatment_ru": ["Шаг 1", "Шаг 2"],
  "prevention": ["Tip 1", "Tip 2"],
  "prevention_ru": ["Совет 1", "Совет 2"],
  "is_healthy": false
}
```

---

## Quick Start (Full Stack)

```bash
# Terminal 1 — Start server
cd server/
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2 — Build & run Android app
# Open android/ in Android Studio → Run on device/emulator
```

---

## Disease Classes

| # | Class | EN Name | RU Name |
|---|-------|---------|---------|
| 0 | healthy | Healthy Plant | Здоровое растение |
| 1 | bacterial_spot | Bacterial Spot | Бактериальная пятнистость |
| 2 | early_blight | Early Blight | Ранний фитофтороз |
| 3 | late_blight | Late Blight | Фитофтороз |
| 4 | leaf_mold | Leaf Mold | Листовая плесень |
| 5 | septoria_leaf_spot | Septoria Leaf Spot | Септориоз |
| 6 | spider_mites | Spider Mites | Паутинный клещ |
| 7 | target_spot | Target Spot | Мишеневидная пятнистость |
| 8 | mosaic_virus | Mosaic Virus | Мозаичный вирус |
| 9 | yellow_leaf_curl | Yellow Leaf Curl | Жёлтое скручивание |
| 10 | powdery_mildew | Powdery Mildew | Мучнистая роса |
| 11 | rust | Rust | Ржавчина |
| 12 | root_rot | Root Rot | Корневая гниль |
| 13 | anthracnose | Anthracnose | Антракноз |
| 14 | botrytis | Gray Mold | Серая гниль |

---

## License

MIT — free for personal and commercial use.
