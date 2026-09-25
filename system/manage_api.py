"""JSON API behind the in-app Settings area.

It drives the admin configuration (ModelAdmin fieldsets, forms, permissions,
actions and activity logging), so the dashboard and /admin/ always show the same
fields, validation and access rules. Only staff users can use it.
"""
import json
from datetime import date, datetime
from functools import wraps

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import flatten_fieldsets, label_for_field, lookup_field
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AdminPasswordChangeForm, ReadOnlyPasswordHashField
from django.db.models.fields.files import FieldFile
from django.forms.widgets import MultiWidget
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import strip_tags

from .stats import dashboard_stats

# key, label, group, model path, kind
RESOURCES = [
    ('roadmap', 'Project roadmap', 'Project', 'system.RoadmapTask', 'roadmap'),
    ('milestones', 'Milestones', 'Project', 'system.Milestone', 'collection'),
    ('roadmap-tasks', 'Roadmap tasks', 'Project', 'system.RoadmapTask', 'collection'),
    ('email', 'Email (SMTP)', 'Configuration', 'system.EmailSettings', 'singleton'),
    ('integrations', 'Integrations & API keys', 'Configuration', 'system.Integration', 'collection'),
    ('dubbing', 'Dubbing engine', 'Configuration', 'dubbing.DubbingSettings', 'singleton'),
    ('system', 'System', 'Configuration', 'system.SystemSettings', 'singleton'),
    ('languages', 'Languages', 'Configuration', 'projects.Language', 'collection'),
    ('users', 'Users', 'People & access', 'auth.User', 'collection'),
    ('groups', 'Access rights (groups)', 'People & access', 'auth.Group', 'collection'),
    ('dubs', 'Dub jobs', 'Operations', 'dubbing.DubJob', 'collection'),
    ('activity', 'Activity log', 'Operations', 'admin.LogEntry', 'collection'),
]
PAGE_SIZE = 100
# Reached from the roadmap page's buttons rather than the side menu.
HIDDEN_IN_NAV = {'milestones', 'roadmap-tasks'}


def _model(path):
    from django.apps import apps

    if path == 'auth.User':
        return get_user_model()
    return apps.get_model(path)


def _resource(key):
    for res in RESOURCES:
        if res[0] == key:
            model = _model(res[3])
            model_admin = admin.site._registry.get(model)
            if model_admin is None:
                raise Http404('Not available.')
            return {'key': res[0], 'label': res[1], 'group': res[2], 'model': model, 'kind': res[4], 'admin': model_admin}
    raise Http404('Unknown settings section.')


def staff_api(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'error': 'Sign in first.'}, status=401)
        if not (user.is_active and user.is_staff):
            return JsonResponse({'error': 'Admins only.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _collect_messages(request):
    return [{'level': m.level_tag, 'text': str(m)} for m in messages.get_messages(request)]


# ---------- value formatting ----------

def _plain(value):
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, FieldFile):
        return value.name.rsplit('/', 1)[-1] if value else ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return value
    if hasattr(value, 'all') and callable(value.all):  # related managers
        return ', '.join(str(v) for v in value.all())
    return strip_tags(str(value))


def _display(name, obj, model_admin):
    try:
        field, attr, value = lookup_field(name, obj, model_admin)
    except Exception:
        return ''
    if field is not None and getattr(field, 'choices', None):
        display = getattr(obj, f'get_{field.name}_display', None)
        if display:
            return display()
    return _plain(value)


def _label(name, model, model_admin):
    try:
        return str(label_for_field(name, model, model_admin))
    except Exception:
        return name.replace('_', ' ').capitalize()


# ---------- forms <-> JSON ----------

def _widget(field):
    widget = field.widget
    return getattr(widget, 'widget', widget) if widget.__class__.__name__ == 'RelatedFieldWidgetWrapper' else widget


def _field_schema(bf):
    field = bf.field
    widget = _widget(field)
    schema = {
        'name': bf.name, 'label': str(bf.label), 'help': strip_tags(str(field.help_text or '')),
        'required': field.required, 'readonly': bool(field.disabled),
    }
    value = bf.value()
    if isinstance(field, ReadOnlyPasswordHashField):
        schema.update(type='readonly', value='Saved securely. Use "Set password" to change it.' if value else 'No password set.')
        return schema
    if isinstance(field, (forms.FileField,)):
        schema.update(type='readonly', value=_plain(value) or 'No file')
        return schema
    if isinstance(field, forms.BooleanField):
        schema.update(type='checkbox', value=bool(value))
    elif isinstance(field, (forms.ModelMultipleChoiceField, forms.MultipleChoiceField)):
        choices = []
        for val, label in field.choices:
            label = str(label)
            parts = label.split(' | ')
            choices.append({'value': str(val), 'label': parts[-1], 'group': ' · '.join(parts[:-1]) if len(parts) > 1 else ''})
        schema.update(type='multiselect', choices=choices, value=[str(v) for v in (value or [])])
    elif isinstance(field, forms.ChoiceField) or isinstance(field, forms.ModelChoiceField):
        schema.update(type='select', choices=[{'value': str(v), 'label': str(l)} for v, l in field.choices],
                      value='' if value is None else str(value))
    elif isinstance(widget, MultiWidget):
        if isinstance(value, list):
            value = ' '.join(v for v in value if v)
        schema.update(type='datetime', value=_plain(value)[:16] if value else '')
    elif isinstance(field, forms.JSONField):
        schema.update(type='json', value=value or '')
    elif isinstance(field, (forms.IntegerField, forms.FloatField, forms.DecimalField)):
        schema.update(type='number', value='' if value in (None, '') else value, step='any' if not isinstance(field, forms.IntegerField) else '1')
    elif isinstance(widget, forms.PasswordInput):
        schema.update(type='password', value='')
    elif isinstance(widget, forms.Textarea):
        schema.update(type='textarea', value=value or '')
    elif isinstance(field, forms.EmailField):
        schema.update(type='email', value=value or '')
    elif isinstance(field, forms.URLField):
        schema.update(type='url', value=value or '')
    else:
        schema.update(type='text', value='' if value is None else str(value))
    return schema


def _form_schema(request, res, form, obj):
    model_admin = res['admin']
    fieldsets = model_admin.get_fieldsets(request, obj)  # UserAdmin returns add_fieldsets when obj is None
    readonly = set(model_admin.get_readonly_fields(request, obj)) if obj is not None else set()
    sections, seen = [], set()
    for title, opts in fieldsets:
        fields = []
        for name in flatten_fieldsets([(title, opts)]):
            seen.add(name)
            if name in form.fields:
                fields.append(_field_schema(form[name]))
            elif obj is not None and (name in readonly or not model_admin.has_change_permission(request, obj)):
                fields.append({'name': name, 'label': _label(name, res['model'], model_admin), 'type': 'readonly',
                               'value': _display(name, obj, model_admin), 'help': ''})
        if fields:
            sections.append({'title': str(title or ''), 'description': strip_tags(str(opts.get('description', ''))),
                             'collapsed': 'collapse' in opts.get('classes', ()), 'fields': fields})
    return sections


def _form_class(request, res, obj):
    model_admin = res['admin']
    change = obj is not None
    return model_admin.get_form(request, obj, change=change)


def _bind(request, res, obj, body):
    FormClass = _form_class(request, res, obj)
    probe = FormClass(instance=obj) if obj is not None else FormClass()
    data = {}
    for name, field in probe.fields.items():
        if field.disabled or name not in body:
            continue
        value = body[name]
        widget = _widget(field)
        if isinstance(widget, MultiWidget):
            dt = parse_datetime(value) if value else None
            data[f'{name}_0'] = dt.date().isoformat() if dt else ''
            data[f'{name}_1'] = dt.time().strftime('%H:%M:%S') if dt else ''
        elif value is None:
            data[name] = ''
        else:
            data[name] = value
    return FormClass(data, instance=obj) if obj is not None else FormClass(data)


def _save(request, res, form, obj):
    model_admin = res['admin']
    change = obj is not None
    new = form.save(commit=False)
    model_admin.save_model(request, new, form, change)
    model_admin.save_related(request, form, [], change)
    message = model_admin.construct_change_message(request, form, None, add=not change)
    (model_admin.log_change if change else model_admin.log_addition)(request, new, message)
    return new


def _errors(form):
    return {k: [e['message'] for e in v] for k, v in form.errors.get_json_data().items()}


def _item_payload(request, res, obj):
    model_admin = res['admin']
    can_change = model_admin.has_change_permission(request, obj)
    form = _form_class(request, res, obj)(instance=obj)
    if not can_change:
        for f in form.fields.values():
            f.disabled = True
    actions = []
    if obj is not None:
        actions = [{'name': n, 'label': strip_tags(str(d))} for n, (_, _, d) in model_admin.get_actions(request).items()
                   if n != 'delete_selected']
    return {
        'kind': res['kind'], 'key': res['key'], 'title': str(obj) if obj is not None else f'New {res["model"]._meta.verbose_name}',
        'id': str(obj.pk) if obj is not None else None,
        'sections': _form_schema(request, res, form, obj),
        'can_change': can_change,
        'can_delete': obj is not None and res['kind'] == 'collection' and model_admin.has_delete_permission(request, obj),
        'actions': actions,
        'extras': _extras(res, obj),
    }


def _extras(res, obj):
    if obj is None:
        return {}
    if res['key'] == 'email':
        return {'can_test': True}
    if res['key'] == 'users':
        return {'can_set_password': True}
    if res['key'] == 'dubs':
        return {'open_in_studio': str(obj.pk)}
    return {}


# ---------- endpoints ----------

@staff_api
def index(request):
    sections = []
    for key, label, group, path, kind in RESOURCES:
        model = _model(path)
        model_admin = admin.site._registry.get(model)
        if model_admin and model_admin.has_view_or_change_permission(request):
            sections.append({'key': key, 'label': label, 'group': group, 'kind': kind,
                             'can_add': kind == 'collection' and model_admin.has_add_permission(request),
                             'hidden': key in HIDDEN_IN_NAV})
    stats = dashboard_stats(request.user)
    if 'dubs' in stats:
        stats['dubs']['recent'] = [
            {'id': str(j.pk), 'name': j.name, 'owner': j.owner.get_username(), 'status': j.status,
             'status_label': j.get_status_display(), 'progress': j.progress, 'target_language': j.target_language,
             'created_at': j.created_at.isoformat()}
            for j in stats['dubs']['recent']
        ]
    return JsonResponse({'sections': sections, 'overview': stats, 'admin_url': '/admin/'})


@staff_api
def section(request, key):
    res = _resource(key)
    model_admin = res['admin']
    if not model_admin.has_view_or_change_permission(request):
        return JsonResponse({'error': "You don't have access to this section."}, status=403)

    if res['kind'] == 'roadmap':
        if request.method != 'GET':
            return JsonResponse({'error': 'Method not allowed.'}, status=405)
        return JsonResponse(_roadmap_payload(request, model_admin))

    if res['kind'] == 'singleton':
        obj = res['model'].load()
        if request.method == 'GET':
            return JsonResponse(_item_payload(request, res, obj))
        if request.method in ('PUT', 'POST'):
            return _update(request, res, obj)
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    if request.method == 'GET':
        qs = model_admin.get_queryset(request)
        q = request.GET.get('q', '').strip()
        if q and model_admin.search_fields:
            qs, _ = model_admin.get_search_results(request, qs, q)
        ordering = model_admin.get_ordering(request) or res['model']._meta.ordering or ['-pk']
        qs = qs.order_by(*ordering)
        total = qs.count()
        offset = max(0, int(request.GET.get('offset', 0) or 0))
        columns = [c for c in model_admin.get_list_display(request) if c not in ('action_checkbox',)]
        rows = []
        for obj in qs[offset:offset + PAGE_SIZE]:
            rows.append({'id': str(obj.pk), 'cells': [_display(c, obj, model_admin) for c in columns]})
        return JsonResponse({
            'kind': 'collection', 'key': key, 'title': res['label'], 'total': total, 'offset': offset,
            'columns': [{'name': c, 'label': _label(c, res['model'], model_admin)} for c in columns],
            'rows': rows, 'searchable': bool(model_admin.search_fields),
            'can_add': model_admin.has_add_permission(request),
            'openable': key != 'activity',
        })
    if request.method == 'POST':
        if not model_admin.has_add_permission(request):
            return JsonResponse({'error': "You can't add these."}, status=403)
        body = _body(request)
        if body is None:
            return JsonResponse({'error': 'Send JSON.'}, status=400)
        form = _bind(request, res, None, body)
        if not form.is_valid():
            return JsonResponse({'error': 'Please fix the highlighted fields.', 'errors': _errors(form)}, status=400)
        obj = _save(request, res, form, None)
        payload = _item_payload(request, res, obj)
        payload['messages'] = [{'level': 'success', 'text': f'Added {obj}.'}]
        return JsonResponse(payload, status=201)
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@staff_api
def new_item(request, key):
    res = _resource(key)
    if res['kind'] != 'collection' or not res['admin'].has_add_permission(request):
        return JsonResponse({'error': "You can't add these."}, status=403)
    return JsonResponse(_item_payload(request, res, None))


def _get_obj(request, res, pk):
    model_admin = res['admin']
    obj = model_admin.get_object(request, str(pk))
    if obj is None or not model_admin.has_view_or_change_permission(request, obj):
        raise Http404('Not found.')
    return obj


def _update(request, res, obj):
    if not res['admin'].has_change_permission(request, obj):
        return JsonResponse({'error': "You can view this but can't change it."}, status=403)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'Send JSON.'}, status=400)
    form = _bind(request, res, obj, body)
    if not form.is_valid():
        return JsonResponse({'error': 'Please fix the highlighted fields.', 'errors': _errors(form)}, status=400)
    obj = _save(request, res, form, obj)
    payload = _item_payload(request, res, res['model'].objects.get(pk=obj.pk))
    payload['messages'] = [{'level': 'success', 'text': 'Saved.'}]
    return JsonResponse(payload)


@staff_api
def item(request, key, pk):
    res = _resource(key)
    if res['kind'] != 'collection' or key == 'activity':
        raise Http404('Not found.')
    obj = _get_obj(request, res, pk)
    model_admin = res['admin']
    if request.method == 'GET':
        return JsonResponse(_item_payload(request, res, obj))
    if request.method in ('PUT', 'POST'):
        return _update(request, res, obj)
    if request.method == 'DELETE':
        if not model_admin.has_delete_permission(request, obj):
            return JsonResponse({'error': "You can't delete this."}, status=403)
        if res['key'] == 'users' and obj.pk == request.user.pk:
            return JsonResponse({'error': "You can't delete your own account here."}, status=400)
        label = str(obj)
        try:
            model_admin.log_deletions(request, res['model'].objects.filter(pk=obj.pk))
        except AttributeError:  # Django < 5.1
            model_admin.log_deletion(request, obj, label)
        model_admin.delete_model(request, obj)
        return JsonResponse({'deleted': True, 'messages': [{'level': 'success', 'text': f'Deleted {label}.'}]})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


@staff_api
def run_action(request, key):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    res = _resource(key)
    body = _body(request) or {}
    actions = res['admin'].get_actions(request)
    name = body.get('action')
    if name not in actions or name == 'delete_selected':
        return JsonResponse({'error': 'Unknown action.'}, status=400)
    ids = [str(i) for i in body.get('ids', [])][:500]
    queryset = res['admin'].get_queryset(request).filter(pk__in=ids)
    func = actions[name][0]
    func(res['admin'], request, queryset)
    return JsonResponse({'messages': _collect_messages(request) or [{'level': 'success', 'text': 'Done.'}]})


@staff_api
def email_test(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    res = _resource('email')
    obj = res['model'].load()
    if not res['admin'].has_change_permission(request, obj):
        return JsonResponse({'error': "You can't change email settings."}, status=403)
    res['admin']._send_test(request, obj)
    return JsonResponse({'messages': _collect_messages(request), **_item_payload(request, res, res['model'].load())})


@staff_api
def set_password(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    res = _resource('users')
    obj = _get_obj(request, res, pk)
    if not res['admin'].has_change_permission(request, obj):
        return JsonResponse({'error': "You can't change this user's password."}, status=403)
    body = _body(request) or {}
    form = AdminPasswordChangeForm(obj, {'password1': body.get('password1', ''), 'password2': body.get('password2', ''),
                                         'usable_password': 'true'})
    if not form.is_valid():
        return JsonResponse({'error': 'Please fix the password.', 'errors': _errors(form)}, status=400)
    form.save()
    res['admin'].log_change(request, obj, 'Changed password.')
    if obj.pk == request.user.pk:
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, obj)
    return JsonResponse({'messages': [{'level': 'success', 'text': f'Password changed for {obj}. Their other sessions were signed out.'}]})


# ---------- project roadmap ----------

def _roadmap_payload(request, task_admin):
    from .models import Milestone, RoadmapTask

    milestone_admin = admin.site._registry.get(Milestone)
    today = timezone.localdate()
    milestones, total, done, current = [], 0, 0, None
    for m in Milestone.objects.prefetch_related('tasks__done_by'):
        tasks = sorted(m.tasks.all(), key=lambda t: (t.order, t.pk))
        m_done = sum(t.done for t in tasks)
        total, done = total + len(tasks), done + m_done
        if tasks and m_done == len(tasks):
            status = 'done'
        elif m.target_date and m.target_date < today:
            status = 'late'
        elif m_done:
            status = 'active'
        else:
            status = 'todo'
        if current is None and status != 'done' and tasks:
            current = {'id': m.pk, 'code': m.code, 'title': m.title}
        milestones.append({
            'id': m.pk, 'code': m.code, 'title': m.title, 'phase': m.phase, 'phase_label': m.get_phase_display(),
            'goal': m.goal, 'target_date': _plain(m.target_date), 'completed_at': _plain(m.completed_at),
            'status': status, 'done': m_done, 'total': len(tasks),
            'tasks': [{
                'id': t.pk, 'title': t.title, 'area': t.area, 'area_label': t.get_area_display(),
                'details': t.details, 'reference': t.reference, 'command': t.command, 'owner': t.owner,
                'due_date': _plain(t.due_date), 'overdue': bool(t.due_date and not t.done and t.due_date < today),
                'done': t.done, 'done_at': _plain(t.done_at),
                'done_by': (t.done_by.get_full_name() or t.done_by.get_username()) if t.done_by else '',
                'notes': t.notes,
            } for t in tasks],
        })
    return {
        'kind': 'roadmap', 'key': 'roadmap', 'title': 'Project roadmap',
        'summary': {'total': total, 'done': done, 'percent': round(100 * done / total) if total else 0,
                    'milestones': len(milestones), 'milestones_done': sum(m['status'] == 'done' for m in milestones)},
        'current': current,
        'milestones': milestones,
        'areas': [{'value': v, 'label': str(l)} for v, l in RoadmapTask.AREA_CHOICES],
        'can_change': task_admin.has_change_permission(request),
        'can_add': task_admin.has_add_permission(request),
        'can_edit_milestones': bool(milestone_admin and milestone_admin.has_view_or_change_permission(request)),
    }


@staff_api
def roadmap_task(request, pk):
    """Tick or untick a task (and optionally save its notes) from the roadmap page."""
    from .models import RoadmapTask

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    task_admin = admin.site._registry.get(RoadmapTask)
    task = RoadmapTask.objects.select_related('milestone').filter(pk=pk).first()
    if task is None:
        raise Http404('Task not found.')
    if not task_admin.has_change_permission(request, task):
        return JsonResponse({'error': "You can't change roadmap tasks."}, status=403)
    body = _body(request)
    if body is None:
        return JsonResponse({'error': 'Send JSON.'}, status=400)

    changed = []
    if 'done' in body and bool(body['done']) != task.done:
        task.set_done(bool(body['done']), request.user)
        changed.append('Marked done' if task.done else 'Reopened')
    if 'notes' in body and str(body['notes'] or '') != task.notes:
        task.notes = str(body['notes'] or '')[:20000]
        changed.append('Updated notes')
    if changed:
        task.save()
        task.milestone.refresh_completion()
        task_admin.log_change(request, task, ', '.join(changed))
    return JsonResponse(_roadmap_payload(request, task_admin))
