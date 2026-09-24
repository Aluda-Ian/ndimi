"""Ndimi model service: serves your own trained models over the HTTP contract Ndimi expects.

In Ndimi: Admin > Integrations > "Ndimi models (custom HTTP)"
    Base URL      https://your-model-server
    Bearer token  the value of NDIMI_MODELS_TOKEN here
Then choose "Ndimi models" for a step in Dubbing settings.

Endpoints (see dubbing/providers/custom.py in the Ndimi app):
    POST /transcribe   multipart: file, language      -> {"language", "words": [{start, end, text, speaker}], "phrase_level"}
    POST /translate    JSON {texts, source, target}    -> {"texts": [...]}
    POST /tts          JSON {text, language, voice}    -> audio/wav
    POST /voices       multipart: files[], name        -> {"voice_id": "default"}
    GET  /health       which models are configured (no token needed)

Run locally:  uvicorn app:app --port 8000
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ndimi_models import BadAudio, Models, NoModel, device, load_config

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
TOKEN = os.getenv('NDIMI_MODELS_TOKEN', '')
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '500'))

app = FastAPI(title='Ndimi model service', version='1.0')
models = Models(load_config())


def require_token(authorization: str = Header(default='')):
    if not TOKEN:
        return  # no token set: open (fine on a private network, not on the internet)
    supplied = authorization.removeprefix('Bearer ').strip()
    if not hmac.compare_digest(supplied, TOKEN):
        raise HTTPException(status_code=401, detail='Missing or wrong bearer token.')


def _unprocessable(e):
    # 422, not 5xx: Ndimi retries 5xx errors, and retrying won't make a missing model appear.
    raise HTTPException(status_code=422, detail=str(e))


@app.get('/health')
def health():
    cfg = models.config
    return {
        'status': 'ok',
        'device': 'gpu' if device() >= 0 else 'cpu',
        'transcribe': sorted(cfg['transcribe']),
        'translate': sorted(cfg['translate']),
        'tts': sorted(cfg['tts']),
        'token_required': bool(TOKEN),
    }


@app.post('/transcribe', dependencies=[Depends(require_token)])
async def transcribe(file: UploadFile = File(...), language: str = Form(default='')):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f'Audio is larger than {MAX_UPLOAD_MB} MB.')
    try:
        return await run_in_threadpool(models.transcribe, data, file.filename or 'audio', language.strip())
    except (NoModel, BadAudio) as e:
        _unprocessable(e)


class TranslateIn(BaseModel):
    texts: list[str]
    source: str = ''
    target: str
    max_chars: list | None = None
    glossary: dict = Field(default_factory=dict)


@app.post('/translate', dependencies=[Depends(require_token)])
async def translate(body: TranslateIn):
    try:
        return await run_in_threadpool(models.translate, body.texts, body.source, body.target, body.glossary)
    except NoModel as e:
        _unprocessable(e)


class TtsIn(BaseModel):
    text: str
    language: str
    voice: str | None = None


@app.post('/tts', dependencies=[Depends(require_token)])
async def tts(body: TtsIn):
    try:
        audio = await run_in_threadpool(models.tts, body.text, body.language, body.voice)
    except NoModel as e:
        _unprocessable(e)
    return Response(content=audio, media_type='audio/wav')


@app.post('/voices', dependencies=[Depends(require_token)])
async def voices(name: str = Form(default=''), language: str = Form(default=''), files: list[UploadFile] = File(default=[])):
    # Voice cloning isn't supported by these models yet; every dub uses the language's trained voice.
    return {'voice_id': 'default'}
