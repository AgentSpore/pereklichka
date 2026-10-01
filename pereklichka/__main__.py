import uvicorn

from pereklichka.app import create_app
from pereklichka.config import Settings

uvicorn.run(create_app(Settings()), host="0.0.0.0", port=8000)
