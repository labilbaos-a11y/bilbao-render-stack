import os
from fastapi import FastAPI, Request, HTTPException

app = FastAPI(title="bilbao-render-stack")

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "changeme")

@app.get("/")
def root():
    return {"status": "ok", "service": "bilbao-render-stack"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/webhook")
def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge) if challenge and challenge.isdigit() else challenge
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()
    print("Webhook recibido:", payload)
    return {"received": True}
