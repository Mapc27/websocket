import asyncio
import json
from typing import List, Dict
import aiohttp

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.templating import Jinja2Templates

# uvicorn main:app


app = FastAPI()

templates = Jinja2Templates(directory="templates")

with open('data.json', 'r', encoding='utf=8') as file:
    cities = json.loads(file.read())


class CitySubscription:
    instances = {}

    def __init__(self, name):
        self.name = name
        self.subscribers: List[WebSocket] = []
        self.instances[name] = self

    def add_subscriber(self, websocket: WebSocket):
        CitySubscription.remove_subscriber(websocket)
        self.subscribers.append(websocket)

    @classmethod
    def get_or_create(cls, city):
        if city in cls.instances:
            return cls.instances[city]
        return CitySubscription(city)

    @classmethod
    def get_websockets(cls, city):
        if city in cls.instances:
            return cls.instances[city].subscribers
        return []

    @classmethod
    def remove_subscriber(cls, websocket):
        for city in cls.instances:
            if websocket in cls.instances[city].subscribers:
                cls.instances[city].subscribers.remove(websocket)

    @classmethod
    def get_city_subs(cls):
        for city in cls.instances:
            yield city, cls.instances[city].subscribers


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    @staticmethod
    async def send_personal_message(message: str, websocket: WebSocket):
        await websocket.send_text(message)

    @staticmethod
    async def send_json(data, websocket: WebSocket):
        await websocket.send_json(data)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


manager = ConnectionManager()


@app.get("/")
async def get(request: Request):
    return templates.TemplateResponse("index.html", {'request': request, "cities": cities})


@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if await send_to_client(data, websocket):
                city_sub = CitySubscription.get_or_create(data)
                city_sub.add_subscriber(websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        CitySubscription.remove_subscriber(websocket)


async def send_to_client(city, websocket):
    coordinates = get_coordinates_by_city(city)
    if coordinates:
        async with aiohttp.ClientSession() as session:
            response = await post_request(coordinates, session)
        data = {}
        for cluster in response['payload']['clusters']:
            for point in cluster['points']:
                data[point['address']] = point['location']
        data['city'] = cities[city]
        await manager.send_json(data, websocket)
        return True


async def post_request(coordinates, session: aiohttp.ClientSession):
    url = 'https://api.tinkoff.ru/geo/withdraw/clusters'
    headers = {'Content-Type': 'application/json'}
    body = {
        "bounds": {
            "bottomLeft": {
                "lat": coordinates[0],
                "lng": coordinates[1]
            },
            "topRight": {
                "lat": coordinates[2],
                "lng": coordinates[3]
            }
        },
        "filters": {
            "banks": [
                "tcs"
            ],
            "showUnavailable": True,
            "currencies": [
                "USD"
            ]
        },
        "zoom": 11
    }
    response = await session.post(url=url, data=json.dumps(body), headers=headers)
    return json.loads(await response.text())


async def refresh_points():
    while True:
        print('-' * 30, 'refresh', '-' * 30)
        await asyncio.sleep(60)
        try:
            generator = CitySubscription.get_city_subs()
            while True:
                city, websockets = next(generator)
                for websocket in websockets:
                    await send_to_client(city, websocket)
        except StopIteration:
            pass


def get_angle_coordinates(coordinates):
    return coordinates[0] - 0.20, coordinates[1] - 0.20, coordinates[0] + 0.20, coordinates[1] + 0.20


def get_coordinates_by_city(city):
    try:
        return get_angle_coordinates(cities[city])
    except KeyError:
        return


loop = asyncio.get_event_loop()
task = loop.create_task(refresh_points())

try:
    loop.run_until_complete(task)
except (asyncio.CancelledError, RuntimeError):
    pass
