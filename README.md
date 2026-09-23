# Ndimi by Jeota Media: dubbing prototype and Django backend

The existing `index.html` remains the browser prototype. The folder now also contains a Django API backend for the production application foundation.

## Django backend setup

PowerShell:

```powershell
Copy-Item .env.example .env
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python manage.py runserver
```

## Accounts and access rights

Access is permission-based and fully configurable in `/admin/`.

- **Groups** (`/admin/` > Authentication > Groups): build roles by ticking permissions. One starter group, **Users**, can only use the dubbing tool. New sign-ups join it.
- **Admin panel access:** tick **Staff status** on a user to let them open `/admin/`. They only see and change what their groups' permissions allow. Superusers can do everything.
- Only superusers can make someone a superuser or edit a superuser's account.
- Treat **Can change user** and **Can change group** as full admin rights. Anyone with them can grant permissions, including to themselves.

Permissions the app checks:

| Permission | What it allows |
| --- | --- |
| System management \| app access \| Can use the dubbing tool | Open the app at `/` |
| System management \| app access \| Can see every user's projects | Projects API returns everyone's projects |
| System management \| app access \| Can use the app during maintenance | Use the app while maintenance mode is on |
| Projects \| demo setup \| Can change demo setup | Gear icon and `POST /api/config/setup/` |
| Projects \| language \| add / change / delete | Manage languages |
| System management \| system settings / email (SMTP) settings / integration | Manage those pages |
| Admin \| log entry \| Can view log entry | See the activity log |

Create your first superuser once:

```powershell
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
```

### Sign-in and account pages

| Page | What it does |
| --- | --- |
| `/accounts/signup/` | Create an account (when sign-up is open). Sends an email confirmation link. |
| `/accounts/login/` | Sign in with a username or email. "Keep me signed in" is optional. 5 wrong passwords lock that login for 15 minutes. |
| `/accounts/logout/` | Sign out. Opening it directly shows a confirmation page first. |
| `/accounts/` | Account page: edit name, username and email, confirm email, change password, sign out other devices, delete account. |
| `/accounts/password/` | Change password while signed in. Other devices are signed out, and a security email is sent. |
| `/accounts/password-reset/` | Forgotten password: emails a reset link (also linked from the admin login page). |
| `/accounts/verify/<token>/` | Email confirmation link (valid for 3 days). |

Admins can turn on **Require email verification** in System settings. Users can't use the dubbing tool until they confirm their email, and superusers are exempt. Existing accounts are treated as already confirmed.

In `/admin/` > Users, admins can select users and **send a password reset email**, **mark email as confirmed**, or **deactivate** them (which blocks sign-in and ends their sessions).

Account emails go out through the SMTP settings. Until email is enabled, they're printed in the server console, where you can copy the links while testing.

## System management (in `/admin/`)

- **System settings:** site name, support email, open or close sign-up, the group new sign-ups join, maintenance mode and its message, and the largest video upload.
- **Email (SMTP) settings:** host, port, security, login and sender. **Save and send a test email** sends a test to your own account's email. Until email is enabled, emails such as password resets are printed in the server console.
- **Integrations:** keys for OpenAI, ElevenLabs, Google Cloud, the AfriVoices-KE dataset, Cloudflare R2, AWS S3, M-Pesa (Daraja), Stripe and Africa's Talking are listed ahead of time. Admins can add others. Keys, secrets and extra secrets are encrypted and never shown again after saving. Non-secret settings (bucket, region, shortcode) go in **Config**.
- **Activity log:** who added, changed or deleted what in the admin panel.

App code reads credentials with:

```python
from system.models import Integration
creds = Integration.credentials('openai')  # None if not enabled
creds['api_key'], creds['config'], creds['secrets']
```

### Encryption key

Secrets are encrypted with `FIELD_ENCRYPTION_KEY` from `.env`, or with a key derived from `DJANGO_SECRET_KEY` if that is empty. Set `FIELD_ENCRYPTION_KEY` before saving any secrets, and back it up. If it changes, saved secrets become unreadable and must be entered again.

## Dubbing engine

Ndimi has two engines. The **Auto** setting picks one for each job:

- **Ndimi pipeline** (every language, editable): prepare → separate voice from music → transcribe with speakers and word timings → translate (context-aware, sized to each line's time slot) → *review* → clone each speaker's voice → generate each line → time-fit (speed-up keeps the pitch, up to 1.3×) → mix onto the music bed, or duck the original → loudness-normalise → render MP4, audio and subtitles.
- **ElevenLabs Dubbing API** (one click): used for the languages listed in Dubbing settings (by default `sw,ki`).

Every stage's provider can be swapped in **Admin > Dubbing settings**:

| Stage | Options |
| --- | --- |
| Speech-to-text | ElevenLabs Scribe (speakers + word timings), OpenAI Whisper, Ndimi models |
| Translation | OpenAI, Google Translate, Ndimi models, none (people translate in review) |
| Voice | ElevenLabs (cloning, `eleven_v3` speaks Swahili and Somali), Ndimi models |
| Separation | Demucs (local, best), ElevenLabs voice isolator, none (ducking) |

**Ndimi models** is a plain HTTP contract (`dubbing/providers/custom.py`) for your own models, for example ones trained on AfriVoices-KE for Dholuo, Kalenjin and Maa. Point the `ndimi-models` integration at the server.

### Run it

1. Install ffmpeg (`winget install ffmpeg`) and restart the terminal. The Dubbing settings page shows whether it's found.
2. `pip install -r requirements.txt` (adds `requests` and `numpy`). Optional: `pip install demucs` for the cleanest music bed.
3. In **Integrations**, switch on and add keys for ElevenLabs, plus OpenAI or Google for translation. Optionally set `translation_model` in the OpenAI config.
4. Keep a worker running next to the web server, in a second terminal:

```powershell
python manage.py run_dub_worker
```

Start more workers for more jobs at once. Jobs survive restarts: finished stages are skipped, temporary provider errors retry with backoff, and ElevenLabs jobs are polled without blocking the worker.

5. Test the whole pipeline with fake providers and real ffmpeg: `python manage.py test dubbing`

### Review and editing

With **Review the script first** on, the job pauses after translation. Edit any line in the studio, then press **Approve script**. After a dub finishes, editing a line marks it "needs new voice", and **Regenerate** re-voices only those lines and re-renders. Lines that still run long after the speed-up are flagged "Too long by Xs · shorten".

### Permissions

*Dubbing | dub job | Can add dub job* lets someone create dubs (the Users group has it). *Can see every user's dub jobs* and *Can choose the dubbing engine per job* are for staff. Cloned voices are deleted from ElevenLabs when a job is deleted.

### Dubbing API

- `GET/POST /api/dubs/`: list or create (multipart `file` or `source_url`, `target_language`, `source_language`, `review`, `clone_voices`, `num_speakers`, `glossary` JSON)
- `GET/DELETE /api/dubs/<id>/`: status, stages, speakers, lines, events
- `PATCH /api/dubs/<id>/segments/<n>/`: `translated_text`, `speaker_id`, `start`, `end`
- `PATCH /api/dubs/<id>/speakers/<pk>/`: `name`, `voice_id`
- `POST /api/dubs/<id>/approve/ | regenerate/ | cancel/ | retry/`
- `GET /api/dubs/<id>/download/video|audio|subtitles|source-subtitles|source/` (add `?download=1` to save the file)

## Settings in the app (admins)

Staff users see **Settings** in the app sidebar. Superusers see every section:

- **Overview:** dub queue, reviews waiting, failures, users, integrations and email status, with warnings when the worker is stopped, email is off, or maintenance mode is on.
- **Configuration:** Email (SMTP) with **Save and send test email**, Integrations & API keys (encrypted and never shown again), Dubbing engine, System, and Languages.
- **People & access:** Users (edit details and groups, set a password, send a reset email, confirm emails, deactivate) and Access rights (groups and their permissions).
- **Operations:** Dub jobs (open one in the studio, retry, cancel) and the Activity log.

`/admin/` opens this Settings dashboard, and admin sign-in uses the app's sign-in page. The classic Django admin is still at `/admin/?classic=1` for advanced use.

These pages are built from the same admin configuration (`system/manage_api.py`, served under `/api/manage/`). They share the admin panel's fields, validation, permissions and activity log, so anything changed in one place shows up in the other. Staff who aren't superusers only see the sections their groups allow.

## Admin panel

`/admin/` uses the Ndimi brand (colours, fonts, logo, light and dark). Its home page is a dashboard showing dubs in progress, reviews waiting, finished and failed dubs, users and integrations. It also warns you when the dub worker seems stopped, email is off, or maintenance mode is on. The templates are in `templates/admin/`.

## API

All endpoints except health need a signed-in session (`401` otherwise) and *Can use the dubbing tool* (`403` otherwise). In maintenance mode they return `503` unless you can bypass it. Calls that change data need the `X-CSRFToken` header.

- `GET /api/health/` checks the Django service and MongoDB connection (public).
- `GET /api/config/` returns the signed-in user, enabled languages, and the demo setup.
- `POST /api/config/setup/` (needs *Can change demo setup*, `403` otherwise) accepts a multipart form with `original_video`, `dubbed_video`, and `script`. Every field is optional.
- `GET /api/projects/` lists your own projects, or everyone's with *Can see every user's projects*.
- `POST /api/projects/` creates a project owned by you. Example body: `{"name":"Meru campaign","target_language":"Kimeru"}`.

Uploaded videos are stored in the `media/` folder and served only to signed-in users.

Start MongoDB locally or set `MONGO_URI` in `.env` to a MongoDB Atlas connection string. MongoDB stores application documents; uploaded media will be added later using object storage such as S3 or Cloudflare R2.

## Upload to a subdomain (cPanel or similar hosting)

1. In your hosting panel, create the subdomain (e.g. `ndimi.jeotamedia.com`). The panel will create a folder for it, often `public_html/ndimi`.
2. Open File Manager, go to that folder, and upload `index.html`.
3. Visit the subdomain in your browser. Make sure SSL/HTTPS is enabled for it (AutoSSL or Let's Encrypt in cPanel).

## Alternative: Netlify (free, about 2 minutes)

1. Go to app.netlify.com/drop and drag this whole folder onto the page.
2. In Site settings > Domain management, add your custom subdomain.
3. At your DNS provider, add the CNAME record Netlify shows you.

## Using the demo

- Sign in with *Can change demo setup* and press **P** (or the gear icon) to open demo setup.
- Load the original English MP4, the dubbed Kiswahili MP4, and paste the script:
  `0:00 | Narrator | English line | Kiswahili line`
- Press Save. The videos upload to the server, and every signed-in user sees them.
- If you open `index.html` without the Django server (for example on Netlify), it falls back to the old offline demo: no sign-in, and videos are saved in that browser only.

## Changing brand colours

Open `index.html` and edit the variables at the top of the `<style>` block
(`--brand`, `--accent`, `--canvas`, etc.). The dark-mode colours are in the two blocks just below.

## Notes

- Fonts load from Google Fonts. Without internet the page falls back to system fonts and still works.
- "Export video" and "Share for review" are placeholders in this prototype.
