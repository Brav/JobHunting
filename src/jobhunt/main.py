"""Entry point for uvicorn: uvicorn --factory jobhunt.main:build_app"""

import logging

from fastapi import FastAPI

from jobhunt.config import load_settings
from jobhunt.web.app import create_app


def build_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
