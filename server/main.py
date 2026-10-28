from fastapi import FastAPI

app = FastAPI(title="Fundamentals Tracker API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"service": "fundamentals-tracker-server", "docs": "/docs"}
