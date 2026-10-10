from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field


ROOM_TTL_SECONDS = 60 * 60 * 6
MAX_QUEUE_ITEMS = 100
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_ms() -> int:
    return int(time.time() * 1000)


def clean_name(value: str | None, fallback: str) -> str:
    cleaned = " ".join((value or "").split()).strip()
    return cleaned[:40] or fallback


class CreateRoomRequest(BaseModel):
    name: str = Field(default="Listening room", min_length=1, max_length=60)
    display_name: str = Field(default="Host", min_length=1, max_length=40)


class JoinRoomRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    display_name: str = Field(default="Guest", min_length=1, max_length=40)


@dataclass
class Member:
    member_id: str
    display_name: str
    is_host: bool = False
    websocket: WebSocket | None = None
    connected_at: float = field(default_factory=time.time)


@dataclass
class Room:
    room_id: str
    code: str
    name: str
    host_id: str
    members: dict[str, Member] = field(default_factory=dict)
    track_id: int | None = None
    position: float = 0.0
    is_playing: bool = False
    queue: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def public_state(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "code": self.code,
            "name": self.name,
            "host_id": self.host_id,
            "track_id": self.track_id,
            "position": self.position,
            "is_playing": self.is_playing,
            "queue": self.queue,
            "server_time": now_ms(),
            "members": [
                {
                    "member_id": member.member_id,
                    "display_name": member.display_name,
                    "is_host": member.member_id == self.host_id,
                    "connected": member.websocket is not None,
                }
                for member in self.members.values()
            ],
        }


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self.code_index: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(6))
            if code not in self.code_index:
                return code

    async def create(self, request: CreateRoomRequest) -> tuple[Room, Member]:
        async with self._lock:
            room_id = uuid4().hex
            code = self._new_code()
            member_id = uuid4().hex
            member = Member(member_id, clean_name(request.display_name, "Host"), True)
            room = Room(room_id, code, clean_name(request.name, "Listening room"), member_id)
            room.members[member_id] = member
            self.rooms[room_id] = room
            self.code_index[code] = room_id
            return room, member

    async def join(self, request: JoinRoomRequest) -> tuple[Room, Member]:
        async with self._lock:
            room_id = self.code_index.get(request.code.strip().upper())
            room = self.rooms.get(room_id or "")
            if not room:
                raise HTTPException(status_code=404, detail="That listening room no longer exists.")
            member = Member(uuid4().hex, clean_name(request.display_name, "Guest"))
            room.members[member.member_id] = member
            room.updated_at = time.time()
            return room, member

    def get(self, room_id: str) -> Room:
        room = self.rooms.get(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Listening room not found.")
        return room

    async def remove_member(self, room: Room, member_id: str) -> None:
        async with room.lock:
            member = room.members.get(member_id)
            if not member:
                return
            member.websocket = None
            room.updated_at = time.time()
            if member_id == room.host_id:
                connected = [item for item in room.members.values() if item.member_id != member_id and item.websocket]
                next_host = min(connected or [], key=lambda item: item.connected_at, default=None)
                if next_host:
                    room.host_id = next_host.member_id
                else:
                    room.is_playing = False
            if all(item.websocket is None for item in room.members.values()):
                room.updated_at = time.time()

    async def cleanup(self) -> None:
        cutoff = time.time() - ROOM_TTL_SECONDS
        async with self._lock:
            expired = [room_id for room_id, room in self.rooms.items() if room.updated_at < cutoff and all(item.websocket is None for item in room.members.values())]
            for room_id in expired:
                room = self.rooms.pop(room_id)
                self.code_index.pop(room.code, None)

    async def broadcast(self, room: Room, message: dict[str, Any], exclude: str | None = None) -> None:
        dead: list[str] = []
        for member in list(room.members.values()):
            if member.member_id == exclude or member.websocket is None:
                continue
            try:
                await member.websocket.send_json(message)
            except Exception:
                dead.append(member.member_id)
        for member_id in dead:
            await self.remove_member(room, member_id)


manager = RoomManager()
router = APIRouter(prefix="/rooms", tags=["Listening rooms"])


async def cleanup_loop() -> None:
    """Keep abandoned in-memory rooms from living forever."""
    while True:
        await asyncio.sleep(300)
        await manager.cleanup()


def session_payload(room: Room, member: Member) -> dict[str, Any]:
    return {
        "room_id": room.room_id,
        "code": room.code,
        "name": room.name,
        "member_id": member.member_id,
        "token": member.member_id,
        "is_host": member.member_id == room.host_id,
        "state": room.public_state(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_room(request: CreateRoomRequest) -> dict[str, Any]:
    room, member = await manager.create(request)
    return session_payload(room, member)


@router.post("/join")
async def join_room(request: JoinRoomRequest) -> dict[str, Any]:
    room, member = await manager.join(request)
    await manager.broadcast(room, {"type": "members", "host_id": room.host_id, "members": room.public_state()["members"]})
    return session_payload(room, member)


@router.get("/{room_id}")
async def room_state(room_id: str) -> dict[str, Any]:
    return manager.get(room_id).public_state()


def host_only(room: Room, member_id: str) -> bool:
    return room.host_id == member_id


def valid_sync(payload: dict[str, Any]) -> bool:
    return (
        (payload.get("track_id") is None or isinstance(payload.get("track_id"), int))
        and isinstance(payload.get("position", 0), (int, float))
        and isinstance(payload.get("is_playing", False), bool)
        and 0 <= float(payload.get("position", 0)) <= 24 * 60 * 60
    )


@router.websocket("/{room_id}/ws")
async def room_socket(websocket: WebSocket, room_id: str, member_id: str, token: str) -> None:
    room = manager.rooms.get(room_id)
    if not room or token != member_id or member_id not in room.members:
        await websocket.close(code=4404)
        return

    member = room.members[member_id]
    await websocket.accept()
    previous_socket = member.websocket
    member.websocket = websocket
    if previous_socket and previous_socket is not websocket:
        try:
            await previous_socket.close(code=4001)
        except Exception:
            pass
    room.updated_at = time.time()
    await websocket.send_json({"type": "room_state", "state": room.public_state()})
    await manager.broadcast(room, {"type": "members", "host_id": room.host_id, "members": room.public_state()["members"]})

    try:
        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                await websocket.send_json({"type": "error", "message": "Malformed room message."})
                continue
            message_type = payload["type"]
            room.updated_at = time.time()

            if message_type == "ping":
                await websocket.send_json({"type": "pong", "server_time": now_ms(), "client_time": payload.get("client_time")})
            elif message_type == "sync":
                if not host_only(room, member_id):
                    await websocket.send_json({"type": "error", "message": "Only the host can control shared playback."})
                    continue
                if not valid_sync(payload):
                    await websocket.send_json({"type": "error", "message": "Invalid playback state."})
                    continue
                room.track_id = payload.get("track_id")
                room.position = float(payload.get("position", 0))
                room.is_playing = bool(payload.get("is_playing", False))
                await manager.broadcast(room, {"type": "sync", "track_id": room.track_id, "position": room.position, "is_playing": room.is_playing, "server_time": now_ms()})
            elif message_type == "queue":
                if not host_only(room, member_id):
                    await websocket.send_json({"type": "error", "message": "Only the host can change the shared queue."})
                    continue
                queue = payload.get("queue")
                if not isinstance(queue, list) or len(queue) > MAX_QUEUE_ITEMS or not all(isinstance(item, dict) for item in queue):
                    await websocket.send_json({"type": "error", "message": "Invalid shared queue."})
                    continue
                room.queue = queue
                await manager.broadcast(room, {"type": "queue", "queue": room.queue, "server_time": now_ms()})
            elif message_type == "play_request":
                if host_only(room, member_id):
                    await websocket.send_json({"type": "error", "message": "Hosts do not need to request playback."})
                else:
                    await manager.broadcast(room, {"type": "play_request", "request_id": uuid4().hex, "member_id": member_id, "display_name": member.display_name, "track_id": payload.get("track_id")}, exclude=member_id)
            elif message_type == "play_request_result":
                if not host_only(room, member_id):
                    await websocket.send_json({"type": "error", "message": "Only the host can approve playback requests."})
                    continue
                await manager.broadcast(room, {"type": "play_request_result", "request_id": payload.get("request_id"), "member_id": payload.get("member_id"), "approved": bool(payload.get("approved"))})
            elif message_type == "leave":
                break
            else:
                await websocket.send_json({"type": "error", "message": "Unknown room message type."})
    except (WebSocketDisconnect, ValueError, TypeError):
        pass
    finally:
        if member.websocket is websocket:
            await manager.remove_member(room, member_id)
            await manager.broadcast(room, {"type": "members", "host_id": room.host_id, "members": room.public_state()["members"]})
