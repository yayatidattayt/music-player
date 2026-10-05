# Music Playlist Manager

A small playlist manager with a dark HTML, CSS, and JavaScript interface backed by FastAPI, SQLAlchemy, and SQLite. Create collections, add songs, and keep their details together.

## Run on Windows PowerShell

Open PowerShell and run:

```powershell
cd "$env:USERPROFILE\Documents\day-5-sdp"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

If PowerShell blocks virtual environment activation, run this once in that terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the playlist manager. The API's interactive documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The database is created automatically in `data/playlists.db` when the server starts.

The interface supports creating, searching, editing, and deleting playlists; adding, filtering, editing, and removing songs; and showing song counts and playlist length. Playlist and song data are saved in SQLite.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Check that the API is running |
| POST | `/api/playlists` | Create a playlist |
| GET | `/api/playlists` | List playlists; optionally search by name |
| GET | `/api/playlists/{playlist_id}` | Get a playlist and all its tracks |
| PATCH | `/api/playlists/{playlist_id}` | Rename a playlist or edit its description |
| DELETE | `/api/playlists/{playlist_id}` | Delete a playlist and its tracks |
| POST | `/api/playlists/{playlist_id}/tracks` | Add a track to a playlist |
| GET | `/api/playlists/{playlist_id}/tracks` | List a playlist's tracks |
| PATCH | `/api/tracks/{track_id}` | Edit a track or move it to another playlist |
| DELETE | `/api/tracks/{track_id}` | Delete a track |

## Example requests

Create a playlist:

```json
{
  "name": "Road Trip",
  "description": "Songs for the drive"
}
```

Add a track to playlist `1`:

```json
{
  "title": "Dreams",
  "artist": "Fleetwood Mac",
  "album": "Rumours",
  "duration_seconds": 257,
  "position": 0
}
```

Playlist and track IDs are returned by the API. Use those IDs in the paths for later edits and deletes.
