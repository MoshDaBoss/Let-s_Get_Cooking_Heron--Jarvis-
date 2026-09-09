# Let-s_Get_Cooking_Jarvis-_Gemini-

This is my attempt to make an all-around, website-capable AI helper.
Currently it is built for the Gemini API, but the code is general enough to transfer to other models and services.
It is still in progress, but it is intended to handle general processing tasks and read Google Docs.

This project is a small Python prototype of a JARVIS-inspired AI operating model with a simple local web app frontend and Gemini-powered backend.

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

## API key setup

You can provide your Gemini API key in either of these ways:

- Type it into the app UI under the Gemini API key field, which saves it in the browser local storage.
- Or place it in a local `.env` file in the project root:

```env
GEMINI_API_KEY=your_key_here
```

The app will use the in-browser key first, and will fall back to the local `.env` file if needed.

## Architecture

The system is organized into three layers:

- JARVIS Brain: understands the user objective, selects tools, and generates a structured plan.
- Local Controller: turns the plan into concrete actions to run in the local runtime.
- Sandboxed VM: hosts the browser and UI/vision capabilities used by the controller.

## Components

- `app.py`: desktop-style launcher for the local app
- `server.py`: backend API server that forwards prompts to Gemini
- `index.html`: main frontend UI
- `app.js`: frontend interaction and key handling
- `jarvis.py`: orchestration logic
- `tests/test_jarvis.py`: regression tests
