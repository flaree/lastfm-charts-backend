import asyncio
import contextlib
import datetime
import json
import math
from io import BytesIO
from pathlib import Path
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFile, ImageFont
from pydantic import BaseModel
from starlette.responses import StreamingResponse

DATATYPES = {
    "albums": "user.gettopalbums",
    "artists": "user.gettopartists",
    # "toptracks": "user.gettoptracks",
    "recenttracks": "user.getrecenttracks",
}
NO_IMAGE_PLACEHOLDER = (
    "https://lastfm.freetls.fastly.net/i/u/300x300/2a96cbd8b46e442fc41c2b86b821562f.png"
)
ImageFile.LOAD_TRUNCATED_IMAGES = True

from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/lastfm/chart/{username}/{datatype}/{period}")
async def lastfm_chart(username: str, datatype: str, period: str, height: int = 3, width: int = 3):
    if height + width > 31:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Height and width cannot be greater than 31",
        )
    if datatype not in DATATYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid datatype",
        )
    url = "http://ws.audioscrobbler.com/2.0/"
    params = {
        "user": username,
        "method": DATATYPES[datatype],
        "period": period,
        "limit": 100,
    }
    params["api_key"] = "API_KEY"
    params["format"] = "json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            with contextlib.suppress(aiohttp.ContentTypeError):
                content = await response.json()
                if "error" in content or response.status != 200:
                    raise HTTPException(status_code=response.status, detail=content["message"])
    chart = await create_chart(content, params["method"], height, width, period, username)
    return StreamingResponse(content=chart, media_type="image/png", status_code=200)


async def create_chart(data, method, height, width, period, username):
    chart = []
    chart_data = {}
    if method == "user.gettopalbums":
        albums = data["topalbums"]["album"]
        for album in albums[: width * height]:
            name = album["name"]
            artist = album["artist"]["name"]
            plays = album["playcount"]
            if album["image"][3]["#text"] in chart_data:
                chart_img = chart_data[album["image"][3]["#text"]]
            else:
                chart_img = await get_img(album["image"][3]["#text"])
                chart_data[album["image"][3]["#text"]] = chart_img
            chart.append(
                (
                    f"{plays} {format_plays(plays)}\n{name} - {artist}",
                    chart_img,
                )
            )
        img = await charts(chart, width, height)

    elif method == "user.gettopartists":
        artists = data["topartists"]["artist"]
        scraped_images = await scrape_artists_for_chart(username, period, width * height)
        iterator = artists[: width * height]
        for i, artist in enumerate(iterator):
            name = artist["name"]
            plays = artist["playcount"]
            if scraped_images[i] in chart_data:
                chart_img = chart_data[scraped_images[i]]
            else:
                chart_img = await get_img(scraped_images[i])
                chart_data[scraped_images[i]] = chart_img
            chart.append(
                (
                    f"{plays} {format_plays(plays)}\n{name}",
                    chart_img,
                )
            )
        img = await charts(chart, width, height)

    elif method == "user.getrecenttracks":
        tracks = data["recenttracks"]["track"]
        if isinstance(tracks, dict):
            tracks = [tracks]
        for track in tracks[: width * height]:
            name = track["name"]
            artist = track["artist"]["#text"]
            if track["image"][3]["#text"] in chart_data:
                chart_img = chart_data[track["image"][3]["#text"]]
            else:
                chart_img = await get_img(track["image"][3]["#text"])
                chart_data[track["image"][3]["#text"]] = chart_img
            chart.append(
                (
                    f"{name} - {artist}",
                    chart_img,
                )
            )
            img = await gen_track_chart(chart, width, height)

    return img


async def charts(data, w, h):
    fnt_file = f"{Path(__file__).resolve().parent}/fonts/Arial Unicode.ttf"
    fnt = ImageFont.truetype(fnt_file, 18, encoding="utf-8")
    imgs = []
    for item in data:
        img = BytesIO(item[1])
        image = Image.open(img).convert("RGBA")
        draw = ImageDraw.Draw(image)
        texts = item[0].split("\n")
        if len(texts[1]) > 30:
            height = 227
            text = f"{texts[0]}\n{texts[1][:30]}\n{texts[1][30:]}"
        else:
            height = 247
            text = item[0]
        draw.text(
            (5, height),
            text,
            fill=(255, 255, 255, 255),
            font=fnt,
            stroke_width=1,
            stroke_fill=(255, 255, 255, 0),
        )
        _file = BytesIO()
        image.save(_file, "png")
        _file.name = f"{item[0]}.png"
        _file.seek(0)
        imgs.append(_file)
    img = create_graph(imgs, w, h)
    return img


async def gen_track_chart(data, w, h):
    fnt_file = f"{Path(__file__).resolve().parent}/fonts/Arial Unicode.ttf"
    fnt = ImageFont.truetype(fnt_file, 18, encoding="utf-8")
    imgs = []
    for item in data:
        img = BytesIO(item[1])
        image = Image.open(img).convert("RGBA")
        draw = ImageDraw.Draw(image)
        if len(item[0]) > 30:
            height = 247
            text = f"{item[0][:30]}\n{item[0][30:]}"
        else:
            height = 267
            text = item[0]
        draw.text(
            (5, height),
            text,
            fill=(255, 255, 255, 255),
            font=fnt,
            stroke_width=1,
            stroke_fill=(255, 255, 255, 0),
        )
        _file = BytesIO()
        image.save(_file, "png")
        _file.name = f"{item[0]}.png"
        _file.seek(0)
        imgs.append(_file)
    img = create_graph(imgs, w, h)
    return img


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i : i + n]


def create_graph(data, w, h):
    dimensions = (300 * w, 300 * h)
    final = Image.new("RGBA", dimensions)
    images = chunks(data, w)
    y = 0
    for chunked in images:
        x = 0
        for img in chunked:
            new = Image.open(img)
            w, h = new.size
            final.paste(new, (x, y, x + w, y + h))
            x += 300
        y += 300
    w, h = final.size
    if w > 2100 and h > 2100:
        final = final.resize(
            (2100, 2100), resample=Image.ANTIALIAS
        )  # Resize cause a 6x6k image is blocking when being sent
    file = BytesIO()
    final.save(file, "webp")
    file.name = f"chart.webp"
    file.seek(0)
    return file


async def scrape_artists_for_chart(username, period, amount):
    period_format_map = {
        "7day": "LAST_7_DAYS",
        "1month": "LAST_30_DAYS",
        "3month": "LAST_90_DAYS",
        "6month": "LAST_180_DAYS",
        "12month": "LAST_365_DAYS",
        "overall": "ALL",
    }
    tasks = []
    url = f"https://www.last.fm/user/{username}/library/artists"
    for i in range(1, math.ceil(amount / 50) + 1):
        params = {"date_preset": period_format_map[period], "page": i}
        task = asyncio.ensure_future(fetch(url, params, handling="text"))
        tasks.append(task)

    responses = await asyncio.gather(*tasks)

    images = []
    for data in responses:
        if len(images) >= amount:
            break
        else:
            soup = BeautifulSoup(data, "html.parser")
            imagedivs = soup.findAll("td", {"class": "chartlist-image"})
            images += [
                div.find("img")["src"].replace("/avatar70s/", "/300x300/") for div in imagedivs
            ]

    return images


async def fetch(url, params=None, handling="json"):
    if params is None:
        params = {}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if handling == "json":
                return await response.json()
            if handling == "text":
                return await response.text()
            return await response


async def get_img(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url or NO_IMAGE_PLACEHOLDER) as resp:
            if resp.status == 200:
                img = await resp.read()
                return img
            async with session.get(NO_IMAGE_PLACEHOLDER) as resp:
                img = await resp.read()
                return img


def format_plays(amount):
    if amount == 1:
        return "play"
    return "plays"
