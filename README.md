# Let-s_Get_Cooking_Jarvis-_Gemini-

<<<<<<< HEAD
This is my attempt to make an all-around, website-capable AI helper.
Currently it is built for the Gemini API, but the code is general enough to transfer to other models and services.
It is still in progress, but it is intended to handle general processing tasks and read Google Docs. I used copliot to physically write out the code but I developed the code logic and flow.

This project is a small Python prototype of a JARVIS-inspired AI operating model with a simple local web app frontend and Gemini-powered backend.
=======
This project is a small Python desktop app with a local web frontend and a configurable AI backend. Gemini is supported by default, and the app can be packaged as a downloadable desktop build.
>>>>>>> 0eb4756 (Package desktop app and fix document uploads)

## Run it locally

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

2. Start the app:

```bash
python3 app.py
```

This opens the app in the browser and launches the local backend server on port 8000.

## Build a downloadable desktop app

Install the dependencies, then build on the operating system where the app will run:

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
python3 build_desktop.py
```

PyInstaller creates the desktop build in `dist/JarvisApp/`. On Linux, run `dist/JarvisApp/JarvisApp`. Place `.env` in that same folder. On Windows and macOS, run the same build command on that operating system to create its native app bundle; desktop builds are platform-specific.

The build command creates a platform-specific ZIP such as `dist/JarvisApp-linux-x86_64.zip` or `dist/JarvisApp-windows-x86_64.zip`. Extract the archive, copy `.env.example` to `.env`, add your API key, and launch the executable in the `JarvisApp` folder. On Windows, launch `JarvisApp.exe`.

To create a Windows download from this repository, run the `Build desktop app` GitHub Actions workflow manually or push a version tag. The workflow builds Linux, Windows, and macOS artifacts on their native runners and publishes each ZIP as a downloadable workflow artifact.

Browser automation requires the Playwright Chromium browser on the target machine. Install it once with `python3 -m playwright install chromium` before using browser tasks.

The packaged app still opens its local interface in the default browser. Copy `.env.example` beside the executable and rename it to `.env` to configure the AI provider.

## API key setup

You can provide your Gemini API key in either of these ways:

- Type it into the app UI under the Gemini API key field, which saves it in the browser local storage.
- Or place it in a local `.env` file in the project root:

```env
GEMINI_API_KEY=your_key_here
```

The app will use the in-browser key first, and will fall back to the local `.env` file if needed.

## AI provider setup

Gemini remains the default. To use any OpenAI-compatible API, set these values in `.env`:

```env
AI_PROVIDER=openai_compatible
AI_API_KEY=your_key_here
AI_BASE_URL=https://api.example.com/v1
AI_MODEL=your-model-name
```

## Architecture

The system is organized into three layers:

- JARVIS Brain: understands the user objective, selects tools, and generates a structured plan.
- Local Controller: turns the plan into concrete actions to run in the local runtime.
- Sandboxed VM: hosts the browser and UI/vision capabilities used by the controller.

## Components

- `app.py`: desktop-style launcher for the local app
- `server.py`: backend API server and provider adapter
- `index.html`: main frontend UI
- `app.js`: frontend interaction and key handling
- `jarvis.py`: orchestration logic
- `tests/test_jarvis.py`: regression tests
