FROM tiangolo/uvicorn-gunicorn-fastapi:python3.7

COPY ./app /app
RUN pip3 install --no-cache-dir bs4 aiohttp pillow