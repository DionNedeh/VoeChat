# VoeChat

VoeChat is a local-first Windows desktop chat shell for `.gguf` models served by a bundled or configured `llama-server.exe`.

The app is designed for offline local model inference first, with optional online web search and URL reading when the user enables the globe button.

## Features

- Dark desktop chat UI built with pywebview and offline-safe HTML/CSS/JS.
- Local model discovery for `.gguf` files and compatible `mmproj` vision projectors.
- llama.cpp `llama-server.exe` lifecycle management through a local subprocess.
- OpenAI-compatible streaming responses with frontend polling.
- Context window estimate tracking without blocking the UI.
- Per-chat local storage, chat deletion, and text export.
- TXT, PDF, DOCX, and image upload support.
- Optional web search and URL reading for online context injection.
- F11 fullscreen support on Windows.

## Run From Source

1. Install dependencies:

   ```powershell
   py -m pip install -r requirements.txt
   ```

2. Put `llama-server.exe` and any required llama.cpp runtime DLLs at `bin\`, or set:

   ```powershell
   $env:VLITE_LLAMA_SERVER="C:\path\to\llama-server.exe"
   ```

3. Start the app:

   ```powershell
   py main.py
   ```

Model files are not bundled. VoeChat scans common local model folders and lets users add more folders from Settings.

## Runtime Binaries

The source repo does not include the full CUDA llama.cpp runtime because some vendor DLLs exceed GitHub's normal file-size limits. Keep the local `bin\` folder for packaging, or download a matching llama.cpp Windows build and place the server/runtime files there before running from source.
