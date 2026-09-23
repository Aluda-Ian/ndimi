from datetime import datetime, timezone
from functools import wraps
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.urls import reverse
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from accounts.models import needs_email_verification
from system.models import SystemSettings

from .models import VIDEO_EXTENSIONS, DemoSetup, Language, youtube_id


# ---------- access control ----------

def api_login_required(view):
	"""Signed-in users only. Returns JSON 401 instead of redirecting."""
	@wraps(view)
	def wrapper(request, *args, **kwargs):
		if not request.user.is_authenticated:
			return JsonResponse({'error': 'Authentication required.', 'login_url': reverse('login')}, status=401)
		return view(request, *args, **kwargs)
	return wrapper


def api_permission_required(perm):
	"""Signed-in users with a specific permission only."""
	def decorator(view):
		@wraps(view)
		@api_login_required
		def wrapper(request, *args, **kwargs):
			if not request.user.has_perm(perm):
				return JsonResponse({'error': "You don't have access to this."}, status=403)
			if needs_email_verification(request.user):
				return JsonResponse({'error': 'Confirm your email first.', 'account_url': reverse('account')}, status=403)
			return view(request, *args, **kwargs)
		return wrapper
	return decorator


USE_TOOL = 'system.use_dubbing_tool'



def _mongo_database():
	client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=1000)
	return client, client[settings.MONGO_DB_NAME]


# ---------- health (public, for uptime checks) ----------

def health(request):
	try:
		client, database = _mongo_database()
		client.admin.command('ping')
		client.close()
		return JsonResponse({'status': 'ok', 'mongodb': 'connected'})
	except PyMongoError:
		return JsonResponse({'status': 'degraded', 'mongodb': 'unavailable'}, status=503)


# ---------- configuration ----------

def _setup_dict(setup):
	return {
		'original_video': setup.original_video.url if setup.original_video else None,
		'dubbed_video': setup.dubbed_video.url if setup.dubbed_video else None,
		'original_url': setup.original_url,
		'dubbed_url': setup.dubbed_url,
		'original_youtube_id': youtube_id(setup.original_url),
		'dubbed_youtube_id': youtube_id(setup.dubbed_url),
		'script': setup.script,
		'updated_at': setup.updated_at.isoformat() if setup.updated_at else None,
	}


@api_permission_required(USE_TOOL)
def app_config(request):
	"""Everything the dubbing tool needs: who is signed in, languages, demo setup."""
	if request.method != 'GET':
		return JsonResponse({'error': 'Method not allowed.'}, status=405)
	user = request.user
	return JsonResponse({
		'user': {
			'username': user.get_username(),
			'is_admin': user.is_staff,
			'admin_url': reverse('admin:index') if user.is_staff else None,
			'can_configure_demo': user.has_perm('projects.change_demosetup'),
			'can_dub': user.has_perm('dubbing.add_dubjob'),
			'can_manage': user.is_active and user.is_staff,
		},
		'dubbing': _dubbing_config(),
		'languages': [lang.as_dict() for lang in Language.objects.filter(enabled=True)],
		'setup': _setup_dict(DemoSetup.load()),
	})


def _dubbing_config():
	from dubbing.models import DubbingSettings

	settings_ = DubbingSettings.load()
	return {
		'review_by_default': settings_.review_by_default,
		'max_duration_minutes': settings_.max_duration_minutes,
	}


def _validate_video(upload):
	ext = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
	if ext not in VIDEO_EXTENSIONS:
		raise ValidationError(f'{upload.name}: use one of {", ".join(VIDEO_EXTENSIONS)}.')
	max_mb = SystemSettings.load().max_video_upload_mb
	if upload.size > max_mb * 1024 * 1024:
		raise ValidationError(f'{upload.name} is larger than {max_mb} MB.')


@api_permission_required('projects.change_demosetup')
def update_setup(request):
	"""Update the demo videos and/or script (multipart form)."""
	if request.method != 'POST':
		return JsonResponse({'error': 'Method not allowed.'}, status=405)

	setup = DemoSetup.load()
	uploads = {field: request.FILES.get(field) for field in ('original_video', 'dubbed_video')}
	try:
		for upload in uploads.values():
			if upload:
				_validate_video(upload)
	except ValidationError as error:
		return JsonResponse({'error': error.messages[0]}, status=400)

	for field, upload in uploads.items():
		if upload:
			old = getattr(setup, field)
			if old:
				old.delete(save=False)
			getattr(setup, field).save(upload.name, upload, save=False)

	# YouTube links: a link is played instead of the uploaded file; an empty value clears it.
	for field in ('original_url', 'dubbed_url'):
		if field in request.POST:
			value = request.POST[field].strip()
			if value and not youtube_id(value):
				return JsonResponse({'error': f'{value} is not a YouTube link. Paste one like https://youtu.be/abc123XYZ00.'}, status=400)
			setattr(setup, field, value)

	if 'script' in request.POST:
		setup.script = request.POST['script']

	setup.updated_by = request.user
	setup.save()
	return JsonResponse({'setup': _setup_dict(setup)})


# ---------- projects ----------

@api_permission_required(USE_TOOL)
def projects(request):
	user = request.user

	if request.method == 'GET':
		# Users see their own projects, unless allowed to see everyone's.
		query = {} if user.has_perm('system.view_all_projects') else {'owner_id': user.pk}
		try:
			client, database = _mongo_database()
			records = list(database.projects.find(query, {'_id': 0}).sort('created_at', -1))
			client.close()
			return JsonResponse({'projects': records})
		except PyMongoError:
			return JsonResponse({'error': 'MongoDB is unavailable.'}, status=503)

	if request.method == 'POST':
		try:
			payload = json.loads(request.body or '{}')
		except json.JSONDecodeError:
			return JsonResponse({'error': 'Request body must be valid JSON.'}, status=400)

		name = str(payload.get('name', '')).strip()
		if not name:
			return JsonResponse({'error': 'name is required.'}, status=400)

		document = {
			'name': name,
			'source_language': payload.get('source_language', 'English'),
			'target_language': payload.get('target_language', 'Kiswahili'),
			'status': 'created',
			'owner_id': user.pk,
			'owner': user.get_username(),
			'created_at': datetime.now(timezone.utc).isoformat(),
		}
		try:
			client, database = _mongo_database()
			result = database.projects.insert_one(document)
			client.close()
			document.pop('_id', None)
			document['id'] = str(result.inserted_id)
			return JsonResponse({'project': document}, status=201)
		except PyMongoError:
			return JsonResponse({'error': 'MongoDB is unavailable.'}, status=503)

	return JsonResponse({'error': 'Method not allowed.'}, status=405)
