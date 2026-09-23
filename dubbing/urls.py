from django.urls import path

from . import api

urlpatterns = [
    path('', api.dubs, name='dubs'),
    path('<uuid:job_id>/', api.dub_detail, name='dub-detail'),
    path('<uuid:job_id>/approve/', api.approve, name='dub-approve'),
    path('<uuid:job_id>/regenerate/', api.regenerate, name='dub-regenerate'),
    path('<uuid:job_id>/cancel/', api.cancel, name='dub-cancel'),
    path('<uuid:job_id>/retry/', api.retry, name='dub-retry'),
    path('<uuid:job_id>/segments/<int:index>/', api.segment_detail, name='dub-segment'),
    path('<uuid:job_id>/segments/<int:index>/audio/', api.segment_audio, name='dub-segment-audio'),
    path('<uuid:job_id>/speakers/<int:speaker_id>/', api.speaker_detail, name='dub-speaker'),
    path('<uuid:job_id>/download/<slug:kind>/', api.download, name='dub-download'),
]
