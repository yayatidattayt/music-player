# Music Playlist Manager

A small playlist manager with a dark HTML, CSS, and JavaScript interface backed by FastAPI, SQLAlchemy, and SQLite. Create collections, add songs, and keep their details together.

## Easiest Windows setup (for someone who has never used Python)

1. Download this repository from GitHub using **Code → Download ZIP**.
2. Extract the ZIP somewhere easy, such as your Desktop.
3. Open the extracted project folder. Its name and location can be anything.
4. Double-click `setup_ydkmusic.bat` and wait for it to finish. It installs Python, the project packages, and FFmpeg when Windows Package Manager is available.
5. Double-click `start_ydkmusic.bat`.
6. The website opens automatically. If it does not, open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Keep the black server window open while using the website. Close that window when finished.

If Windows shows a security warning, choose **More info → Run anyway** only if the file came from your trusted copy of this repository. If setup says Python was installed but cannot find it, close the window, open the folder again, and run `setup_ydkmusic.bat` one more time.

## Manual Windows PowerShell setup

Open PowerShell and run:

```powershell
cd "C:\path\to\the\extracted\project-folder"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

If PowerShell blocks virtual environment activation, run this once in that terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the playlist manager. The API's interactive documentation is at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The database is created automatically in `data/playlists.db` when the server starts.

### Optional remote audio storage

To avoid keeping permanent copies of uploaded or YouTube-imported audio on the computer, configure a Cloudflare R2 bucket in a `.env` file in the project folder:

```env
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=your-r2-access-key
R2_SECRET_ACCESS_KEY=your-r2-secret-key
R2_BUCKET=ydkmusic-audio
R2_REGION=auto
```

When all five values are present, new audio is uploaded to R2 and the temporary local copy is removed. Playback uses short-lived signed URLs. If R2 is not configured, the app keeps using its local-storage fallback so an offline/local setup still works. Existing local tracks are not migrated automatically.

### Lyrics lookup

The player checks LRCLIB for synced lyrics first and saves a successful match to the track. To enable Musixmatch as an additional automatic source, set `MUSIXMATCH_API_KEY` in `.env` for local use or in the Render service environment. When no automatic match is available, the lyrics view offers direct LRCLIB, Musixmatch, and Genius searches.

### Online deployment with Render

The repository includes `render.yaml` for a basic Render deployment. It creates a web service and a PostgreSQL database. To deploy:

1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and select the repository.
3. Review the `ydkmusic` web service and `ydkmusic-db` PostgreSQL database.
4. Add the R2 secrets under the web service environment variables: `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET`. Add `MUSIXMATCH_API_KEY` if you want Musixmatch automatic lyrics lookup.
5. Deploy and open the generated `onrender.com` URL.

The service uses `DATABASE_URL` from the managed PostgreSQL database. Do not upload the local SQLite database, `.env` file, passwords, or API keys to GitHub. R2 should be configured before importing audio because hosted web-service filesystems are not a permanent place for user media.

### First administrator setup

The existing pre-account library is kept unassigned until you choose its administrator. Stop the server once, then run this from the project folder:

```powershell
.\.venv\Scripts\python.exe setup_admin.py
```

Enter the administrator's email, display name, and password when prompted. If that email already has an account, its current password must be entered before it can be promoted. The setup assigns only legacy, unassigned playlists and listening history to that administrator; it does not move another user's private library. New signups start with an empty library, and each account's data persists in `data/playlists.db` on that computer.

On Windows, you can also double-click `start_ydkmusic.bat`. It reuses a healthy server already running on port 8000 and uses port 8001 if that port belongs to another process.

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

## Music analysis

Song analysis uses music metadata services for tempo, musical key, artwork, and
related track details. [Music analysis data by GetSongBPM](https://getsongbpm.com)
is used as an online reference for BPM and key information.

## Song Lab providers

Song Lab uses online catalog/web sources when available. For authoritative BPM
and key results, set `GETSONGBPM_API_KEY` before starting the server. When a
local audio file is attached, embedded tags are read and librosa estimates BPM
and key when the optional audio packages are installed.

```powershell
$env:GETSONGBPM_API_KEY = "your-api-key"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```
