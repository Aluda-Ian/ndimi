"""Where dub files live.

Locally (no R2 settings) everything is on disk under MEDIA_ROOT, as before.
In production the default storage is Cloudflare R2 (see config/settings.py), so the
website on Vercel and the dub worker on its own server share the same files:

- browsers upload straight to R2 with a signed URL (Vercel can't take large uploads),
- the worker copies what it needs into its local work folder, processes it and saves results back,
- downloads redirect to short-lived signed R2 links.
"""
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponseRedirect

UPLOAD_PREFIX = 'dubbing/uploads'


def is_remote(storage=None):
    storage = storage or default_storage
    try:
        storage.path('')
        return False
    except NotImplementedError:
        return True


def cache_path(field, workdir):
    """Where the worker keeps its local copy of a stored file."""
    return Path(workdir) / 'cache' / field.name.replace('/', '__')


def local_path(field, workdir):
    """A path on this machine for a FileField: the file itself on disk storage, else a cached download."""
    if not is_remote(field.storage):
        return field.path
    target = cache_path(field, workdir)
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    with field.storage.open(field.name, 'rb') as src, open(partial, 'wb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    partial.replace(target)
    return str(target)


def keep_local_copy(field, path, workdir):
    """After saving `path` into `field`, keep it as the local cached copy instead of deleting it."""
    if not is_remote(field.storage):
        Path(path).unlink(missing_ok=True)
        return
    target = cache_path(field, workdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), target)


def serve(field, *, as_attachment=False, content_type=None):
    """Stream from disk, or redirect to a signed R2 link (Vercel can't stream big files)."""
    name = Path(field.name).name
    if is_remote(field.storage):
        params = {}
        if as_attachment:
            params['ResponseContentDisposition'] = f'attachment; filename="{name}"'
        if content_type:
            params['ResponseContentType'] = content_type
        return HttpResponseRedirect(field.storage.url(field.name, parameters=params or None))
    kwargs = {'content_type': content_type} if content_type else {}
    return FileResponse(field.open('rb'), as_attachment=as_attachment, filename=name, **kwargs)


def serve_name(name, content_type=None):
    """Same as serve() for a bare storage path (demo videos)."""
    if is_remote():
        params = {'ResponseContentType': content_type} if content_type else None
        return HttpResponseRedirect(default_storage.url(name, parameters=params))
    return FileResponse(default_storage.open(name, 'rb'), content_type=content_type or 'application/octet-stream')


def new_upload_key(user, filename):
    safe = Path(filename).name.replace(' ', '_')[-120:] or 'upload'
    return f'{UPLOAD_PREFIX}/{user.pk}/{uuid.uuid4().hex}/{safe}'


def upload_key_belongs_to(user, key):
    return key.startswith(f'{UPLOAD_PREFIX}/{user.pk}/') and '..' not in key


def presigned_put(key, content_type, expires=3600):
    """Signed URL the browser PUTs the file to (R2 only)."""
    client = default_storage.connection.meta.client
    return client.generate_presigned_url(
        'put_object',
        Params={'Bucket': default_storage.bucket_name, 'Key': key, 'ContentType': content_type},
        ExpiresIn=expires,
    )


def delete_prefix(prefix):
    """Delete every stored file under a folder (best effort)."""
    if not is_remote():
        shutil.rmtree(Path(settings.MEDIA_ROOT) / prefix, ignore_errors=True)
        return
    bucket = default_storage.bucket
    keys = [{'Key': obj.key} for obj in bucket.objects.filter(Prefix=prefix.rstrip('/') + '/')]
    for i in range(0, len(keys), 1000):
        bucket.delete_objects(Delete={'Objects': keys[i:i + 1000], 'Quiet': True})
