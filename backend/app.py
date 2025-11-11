import json
import secrets
import time
from typing import Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
import asyncio

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# room -> {"client": WebSocket|None, "operator": WebSocket|None, "created_at": timestamp, "operator_ready": bool}
rooms: Dict[str, Dict] = {}

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

@app.get("/create-room")
def create_room(custom_room: Optional[str] = None):
    """Operator xona yaratadi"""
    if custom_room:
        if custom_room in rooms and rooms[custom_room]["operator"] is not None:
            return JSONResponse(
                {"error": "Room already exists", 
                 "message": f"Room '{custom_room}' is already in use. Please choose another ID."},
                status_code=409
            )
        room = custom_room
    else:
        room = secrets.token_urlsafe(6)
    
    rooms[room] = {
        "client": None, 
        "operator": None, 
        "created_at": time.time(),
        "operator_ready": False
    }
    
    return JSONResponse({
        "room": room,
        "client_url": f"/static/client.html?room={room}",
        "operator_url": f"/static/operator.html?room={room}"
    })

@app.get("/check-room/{room}")
def check_room(room: str):
    """Xona mavjudligini tekshirish"""
    if room not in rooms:
        return JSONResponse({"exists": False, "message": "Room not found"}, status_code=404)
    
    return JSONResponse({
        "exists": True,
        "has_operator": rooms[room]["operator"] is not None and rooms[room]["operator_ready"],
        "has_client": rooms[room]["client"] is not None
    })

@app.websocket("/ws/{room}/{role}")
async def ws(room: str, role: str, socket: WebSocket):
    await socket.accept()
    
    if role not in ("client", "operator"):
        await socket.send_text(json.dumps({"type": "error", "message": "Invalid role"}))
        await socket.close()
        return
    
    # OPERATOR
    if role == "operator":
        if room not in rooms:
            # Operator yangi xona ochadi
            rooms[room] = {
                "client": None, 
                "operator": None, 
                "created_at": time.time(),
                "operator_ready": False
            }
        
        # Operator'ni saqlash
        rooms[room]["operator"] = socket
        
        # Operator tayyor bo'lguncha kutish (2 soniya)
        await asyncio.sleep(0.5)
        rooms[room]["operator_ready"] = True
        
        await socket.send_text(json.dumps({
            "type": "connected",
            "role": "operator",
            "room": room,
            "message": "Waiting for client..."
        }))
        
        # Agar client allaqachon kutayotgan bo'lsa
        if rooms[room]["client"]:
            try:
                await rooms[room]["client"].send_text(json.dumps({
                    "type": "peer_connected",
                    "peer_role": "operator"
                }))
                await socket.send_text(json.dumps({
                    "type": "peer_connected",
                    "peer_role": "client"
                }))
            except:
                pass
    
    # CLIENT
    elif role == "client":
        # Xona mavjudligini tekshirish
        if room not in rooms:
            await socket.send_text(json.dumps({
                "type": "error",
                "message": f"Room '{room}' does not exist. Please check the Room ID."
            }))
            await socket.close()
            return
        
        # Operator tayyor emasligini tekshirish
        if not rooms[room]["operator_ready"]:
            await socket.send_text(json.dumps({
                "type": "error",
                "message": f"Operator is not ready yet. Please wait and try again."
            }))
            await socket.close()
            return
        
        # Operator yo'qligini tekshirish
        if rooms[room]["operator"] is None:
            await socket.send_text(json.dumps({
                "type": "error",
                "message": f"No operator in room '{room}'. Please ask operator to connect first."
            }))
            await socket.close()
            return
        
        # Client'ni saqlash
        rooms[room]["client"] = socket
        
        await socket.send_text(json.dumps({
            "type": "connected",
            "role": "client",
            "room": room
        }))
        
        # Operator'ga xabar
        if rooms[room]["operator"]:
            try:
                await rooms[room]["operator"].send_text(json.dumps({
                    "type": "peer_connected",
                    "peer_role": "client",
                    "message": "Client connected successfully!"
                }))
            except:
                pass
    
    peer_role = "operator" if role == "client" else "client"

    # Message loop
    try:
        while True:
            msg = await socket.receive_text()
            other = rooms[room].get(peer_role)
            if other:
                try:
                    await other.send_text(msg)
                except Exception as e:
                    print(f"Send to peer error: {e}")
                    pass
    except WebSocketDisconnect:
        print(f"{role} disconnected from {room}")
    except Exception as e:
        print(f"WebSocket error ({role}): {e}")
    finally:
        # Cleanup
        if role in rooms.get(room, {}):
            rooms[room][role] = None
            
            if role == "operator":
                rooms[room]["operator_ready"] = False
        
        # Peer'ga disconnect xabari
        other = rooms[room].get(peer_role) if room in rooms else None
        if other:
            try:
                await other.send_text(json.dumps({
                    "type": "peer_disconnected",
                    "peer_role": role
                }))
            except:
                pass
        
        # Agar ikkala tomon ham yo'q bo'lsa, xonani o'chirish
        if room in rooms:
            if rooms[room]["client"] is None and rooms[room]["operator"] is None:
                del rooms[room]
                print(f"Room {room} deleted")

app.mount("/static", StaticFiles(directory="static", html=True), name="static")
