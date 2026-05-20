# llama.cpp Runtime

Place `llama-server.exe` and the matching llama.cpp runtime DLLs in this folder when running from source or packaging the Windows app.

The local development copy may contain a full llama.cpp Windows build, but the largest CUDA vendor DLLs are not stored in this GitHub repo because they exceed normal GitHub file-size limits. CPU runtime builds or release artifacts can still be bundled during installer packaging.

You can also set `VLITE_LLAMA_SERVER` to point at an external `llama-server.exe` path.
