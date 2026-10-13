"""Start Before You Sign: python run.py, then open http://127.0.0.1:8000"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Before You Sign is starting. Open http://127.0.0.1:{port} in your browser.\n")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="warning")
